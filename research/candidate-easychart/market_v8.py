"""Repeated-wick liquidity-pool episodes for candidate-easychart v8.

A recurring ambiguity in the source material is the word ``meaningful``:
meaningful highs/lows, obvious support/resistance and intentionally accumulated
liquidity.  A human sees a shelf after repeated separated wick rejections, not
after every local pivot.  This module adapts the MIT-licensed PyIndicators
liquidity-pool mechanism (coding-kitties/PyIndicators) into a small causal state
machine for this project:

stable body boundary -> separated wick contacts -> confirmed liquidity zone ->
first sweep/reclaim -> first boundary retest -> nearest already-observed
opposing pool or structural swing.

The detector does not use future extrema.  A pool is known only after its
required contacts and post-contact confirmation bars have closed.  Two
consecutive closes through the outer edge mitigate it, matching the reused
reference implementation's lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from domain_v3 import ArmedSetup, Candle, Side, TargetMode
from market_v4 import StructuralPivot


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    pool_id: str
    symbol: str
    side: Side
    observed_time_ns: int
    origin_time_ns: int
    origin_index: int
    zone_low: float
    zone_high: float
    contacts: int
    source_timeframe_minutes: int

    def __post_init__(self) -> None:
        if not self.pool_id or not self.symbol:
            raise ValueError("pool identifiers must be non-empty")
        if not all(math.isfinite(value) for value in (self.zone_low, self.zone_high)):
            raise ValueError("pool bounds must be finite")
        if self.zone_high <= self.zone_low:
            raise ValueError("pool high must exceed pool low")
        if self.contacts < 2:
            raise ValueError("a liquidity pool requires repeated contacts")
        if self.observed_time_ns < self.origin_time_ns:
            raise ValueError("pool cannot be observed before its origin")

    @property
    def inner_boundary(self) -> float:
        return self.zone_high if self.side is Side.LONG else self.zone_low

    @property
    def outer_boundary(self) -> float:
        return self.zone_low if self.side is Side.LONG else self.zone_high


@dataclass(slots=True)
class PoolReference:
    side: Side
    origin_index: int
    origin_time_ns: int
    inner: float
    outer: float
    contacts: int = 1
    last_contact_index: int = 0
    emitted: bool = False


@dataclass(frozen=True, slots=True)
class PoolDetectorConfig:
    contact_count: int = 2
    gap_bars: int = 5
    confirmation_bars: int = 10
    mitigation_closes: int = 2
    source_timeframe_minutes: int = 5

    def __post_init__(self) -> None:
        if self.contact_count < 2:
            raise ValueError("contact_count must be at least two")
        if self.gap_bars < 1:
            raise ValueError("gap_bars must be positive")
        if self.confirmation_bars < 1:
            raise ValueError("confirmation_bars must be positive")
        if self.mitigation_closes < 1:
            raise ValueError("mitigation_closes must be positive")
        if self.source_timeframe_minutes <= 0:
            raise ValueError("source timeframe must be positive")


@dataclass(frozen=True, slots=True)
class PoolDetectorUpdate:
    formed: tuple[LiquidityPool, ...]
    mitigated: tuple[LiquidityPool, ...]


class WickLiquidityPoolDetector:
    """Online repeated-wick pool detector adapted from PyIndicators."""

    def __init__(self, symbol: str, config: PoolDetectorConfig) -> None:
        self.symbol = symbol
        self.config = config
        self.high_ref: PoolReference | None = None
        self.low_ref: PoolReference | None = None
        self.active: dict[str, LiquidityPool] = {}
        self.break_counts: dict[str, int] = {}
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _count(self, key: str, amount: int = 1) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + amount

    def _new_id(self, side: Side, origin_index: int) -> str:
        self.sequence += 1
        return f"pool-{self.symbol}-{side.name}-{origin_index}-{self.sequence:08d}"

    @staticmethod
    def _body(candle: Candle) -> tuple[float, float]:
        return max(candle.open, candle.close), min(candle.open, candle.close)

    def _new_high_ref(self, candle: Candle, index: int, body_top: float) -> None:
        self.high_ref = PoolReference(
            side=Side.SHORT,
            origin_index=index,
            origin_time_ns=candle.ts_close_ns,
            inner=body_top,
            outer=candle.high,
            contacts=1,
            last_contact_index=index,
        )
        self._count("high_reference_reset")

    def _new_low_ref(self, candle: Candle, index: int, body_bottom: float) -> None:
        self.low_ref = PoolReference(
            side=Side.LONG,
            origin_index=index,
            origin_time_ns=candle.ts_close_ns,
            inner=body_bottom,
            outer=candle.low,
            contacts=1,
            last_contact_index=index,
        )
        self._count("low_reference_reset")

    @staticmethod
    def _overlap(a: LiquidityPool, b: LiquidityPool) -> bool:
        return max(a.zone_low, b.zone_low) <= min(a.zone_high, b.zone_high)

    def _activate(self, pool: LiquidityPool) -> bool:
        # A newer overlapping zone represents the same visible shelf.  Keep
        # the already-known pool to preserve its original causal identity and
        # avoid multiplying one pool into several trades.
        for existing in self.active.values():
            if existing.side is pool.side and self._overlap(existing, pool):
                self._count("overlapping_pool_suppressed")
                return False
        self.active[pool.pool_id] = pool
        self.break_counts[pool.pool_id] = 0
        self._count("pools_formed")
        self._count(f"pools_formed_{pool.side.name.lower()}")
        return True

    def _formed_pool(
        self,
        reference: PoolReference,
        current: Candle,
    ) -> LiquidityPool:
        low = min(reference.inner, reference.outer)
        high = max(reference.inner, reference.outer)
        return LiquidityPool(
            pool_id=self._new_id(reference.side, reference.origin_index),
            symbol=self.symbol,
            side=reference.side,
            observed_time_ns=current.ts_close_ns,
            origin_time_ns=reference.origin_time_ns,
            origin_index=reference.origin_index,
            zone_low=low,
            zone_high=high,
            contacts=reference.contacts,
            source_timeframe_minutes=self.config.source_timeframe_minutes,
        )

    def _mitigations(self, current: Candle) -> list[LiquidityPool]:
        mitigated: list[LiquidityPool] = []
        for pool_id, pool in list(self.active.items()):
            broken = (
                current.close < pool.zone_low
                if pool.side is Side.LONG
                else current.close > pool.zone_high
            )
            self.break_counts[pool_id] = self.break_counts.get(pool_id, 0) + 1 if broken else 0
            if self.break_counts[pool_id] >= self.config.mitigation_closes:
                mitigated.append(pool)
                self.active.pop(pool_id, None)
                self.break_counts.pop(pool_id, None)
                self._count("pools_mitigated")
                self._count(f"pools_mitigated_{pool.side.name.lower()}")
        return mitigated

    def on_candle(self, candle: Candle, index: int) -> PoolDetectorUpdate:
        body_top, body_bottom = self._body(candle)
        mitigated = self._mitigations(candle)

        if self.high_ref is None:
            self._new_high_ref(candle, index, body_top)
        if self.low_ref is None:
            self._new_low_ref(candle, index, body_bottom)
        assert self.high_ref is not None and self.low_ref is not None

        high_ref = self.high_ref
        if candle.high > high_ref.outer and (
            body_top > high_ref.outer or body_top < high_ref.inner
        ):
            self._new_high_ref(candle, index, body_top)
            high_ref = self.high_ref
        low_ref = self.low_ref
        if candle.low < low_ref.outer and (
            body_bottom < low_ref.outer or body_bottom > low_ref.inner
        ):
            self._new_low_ref(candle, index, body_bottom)
            low_ref = self.low_ref
        assert high_ref is not None and low_ref is not None

        high_contact = candle.high > high_ref.inner and body_top <= high_ref.inner
        if high_contact and index != high_ref.origin_index:
            high_ref.outer = max(high_ref.outer, candle.high)
            if index - high_ref.last_contact_index >= self.config.gap_bars:
                high_ref.contacts += 1
                high_ref.last_contact_index = index
                self._count("high_wick_contacts")

        low_contact = candle.low < low_ref.inner and body_bottom >= low_ref.inner
        if low_contact and index != low_ref.origin_index:
            low_ref.outer = min(low_ref.outer, candle.low)
            if index - low_ref.last_contact_index >= self.config.gap_bars:
                low_ref.contacts += 1
                low_ref.last_contact_index = index
                self._count("low_wick_contacts")

        formed: list[LiquidityPool] = []
        if (
            not high_ref.emitted
            and high_ref.contacts >= self.config.contact_count
            and index - high_ref.last_contact_index >= self.config.confirmation_bars
            and candle.close < high_ref.inner
        ):
            high_ref.emitted = True
            pool = self._formed_pool(high_ref, candle)
            if self._activate(pool):
                formed.append(pool)

        if (
            not low_ref.emitted
            and low_ref.contacts >= self.config.contact_count
            and index - low_ref.last_contact_index >= self.config.confirmation_bars
            and candle.close > low_ref.inner
        ):
            low_ref.emitted = True
            pool = self._formed_pool(low_ref, candle)
            if self._activate(pool):
                formed.append(pool)

        return PoolDetectorUpdate(tuple(formed), tuple(mitigated))


@dataclass(slots=True)
class PoolInteractionState:
    pool: LiquidityPool
    outside: bool = False
    outside_first_index: int | None = None
    extreme: float | None = None
    completed: bool = False


@dataclass(frozen=True, slots=True)
class PoolTrapConfig:
    detector: PoolDetectorConfig = PoolDetectorConfig()
    enable_immediate_fakeout: bool = True
    enable_delayed_trap: bool = True
    tick_size: float = 0.1

    def __post_init__(self) -> None:
        if not (self.enable_immediate_fakeout or self.enable_delayed_trap):
            raise ValueError("at least one pool interaction family must be enabled")
        if not math.isfinite(self.tick_size) or self.tick_size <= 0.0:
            raise ValueError("tick_size must be positive")


class EasyChartLiquidityPoolEngine:
    """Confirmed pool -> sweep/reclaim -> first retest trade policy."""

    def __init__(self, symbol: str, config: PoolTrapConfig) -> None:
        self.symbol = symbol
        self.config = config
        self.detector = WickLiquidityPoolDetector(symbol, config.detector)
        self.states: dict[str, PoolInteractionState] = {}
        self.latest_high: StructuralPivot | None = None
        self.latest_low: StructuralPivot | None = None
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _count(self, key: str, amount: int = 1) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + amount

    def _new_id(self, prefix: str) -> str:
        self.sequence += 1
        return f"ec8-{self.symbol}-{prefix}-{self.sequence:08d}"

    def add_structural_pivot(self, pivot: StructuralPivot) -> None:
        if pivot.side == "HIGH":
            self.latest_high = pivot
        else:
            self.latest_low = pivot
        self._count(f"structural_pivot_{pivot.side.lower()}")

    def _target(
        self,
        pool: LiquidityPool,
        current: Candle,
    ) -> tuple[float, str] | None:
        entry = pool.inner_boundary
        candidates: list[tuple[float, str]] = []
        if pool.side is Side.LONG:
            for opposing in self.detector.active.values():
                if (
                    opposing.side is Side.SHORT
                    and opposing.observed_time_ns < current.ts_close_ns
                    and opposing.zone_low > entry
                ):
                    candidates.append((opposing.zone_low, f"OPPOSING_POOL:{opposing.pool_id}"))
            if (
                self.latest_high is not None
                and self.latest_high.observed_time_ns < current.ts_close_ns
                and self.latest_high.level > entry
            ):
                candidates.append((self.latest_high.level, f"STRUCTURAL_HIGH:{self.latest_high.event_time_ns}"))
            return min(candidates, default=None, key=lambda item: item[0])

        for opposing in self.detector.active.values():
            if (
                opposing.side is Side.LONG
                and opposing.observed_time_ns < current.ts_close_ns
                and opposing.zone_high < entry
            ):
                candidates.append((opposing.zone_high, f"OPPOSING_POOL:{opposing.pool_id}"))
        if (
            self.latest_low is not None
            and self.latest_low.observed_time_ns < current.ts_close_ns
            and self.latest_low.level < entry
        ):
            candidates.append((self.latest_low.level, f"STRUCTURAL_LOW:{self.latest_low.event_time_ns}"))
        return max(candidates, default=None, key=lambda item: item[0])

    def _build_setup(
        self,
        state: PoolInteractionState,
        current: Candle,
        interaction: str,
    ) -> ArmedSetup | None:
        pool = state.pool
        if state.extreme is None:
            raise AssertionError("interaction extreme is required")
        target_item = self._target(pool, current)
        if target_item is None:
            self._count("no_opposing_objective")
            return None
        target, target_id = target_item
        entry = pool.inner_boundary
        stop = (
            state.extreme - self.config.tick_size
            if pool.side is Side.LONG
            else state.extreme + self.config.tick_size
        )
        if pool.side is Side.LONG:
            if current.high >= target:
                self._count("objective_consumed_on_reclaim")
                return None
            if not stop < entry < target:
                self._count("invalid_long_geometry")
                return None
        else:
            if current.low <= target:
                self._count("objective_consumed_on_reclaim")
                return None
            if not target < entry < stop:
                self._count("invalid_short_geometry")
                return None

        family = f"WICK_POOL_{interaction}"
        setup = ArmedSetup(
            setup_id=self._new_id("setup"),
            causal_event_id=f"{family}:{pool.pool_id}:{current.ts_close_ns}",
            symbol=self.symbol,
            family=family,
            side=pool.side,
            observed_time_ns=current.ts_close_ns,
            entry=entry,
            stop=stop,
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=target,
            fixed_target_id=target_id,
            source_pool_id=pool.pool_id,
            zone_low=pool.zone_low,
            zone_high=pool.zone_high,
            formation_extreme=pool.outer_boundary,
            body_ratio=0.0,
            previous_body=0.0,
            current_body=0.0,
            context_bias=f"REPEATED_WICK_CONTACTS:{pool.contacts}",
            source_timeframe_minutes=pool.source_timeframe_minutes,
        )
        if setup.executable(target, target_id=target_id, min_gross_rr=1.0) is None:
            self._count("gross_rr_lt_1")
            return None
        self._count("setups_armed")
        self._count(f"setups_armed_{family}")
        return setup

    def _observe_pool(
        self,
        state: PoolInteractionState,
        current: Candle,
        index: int,
    ) -> ArmedSetup | None:
        pool = state.pool
        if state.completed or pool.observed_time_ns >= current.ts_open_ns:
            return None
        inner, outer = pool.inner_boundary, pool.outer_boundary

        if state.outside:
            assert state.extreme is not None
            if pool.side is Side.LONG:
                state.extreme = min(state.extreme, current.low)
                reclaimed = current.close >= inner
            else:
                state.extreme = max(state.extreme, current.high)
                reclaimed = current.close <= inner
            if reclaimed and self.config.enable_delayed_trap:
                state.completed = True
                self._count("delayed_pool_reclaims")
                return self._build_setup(state, current, "DELAYED_TRAP_RETEST")
            return None

        if pool.side is Side.LONG:
            swept = current.low < outer
            reclaimed = current.close >= inner
            outside_close = current.close < outer
            extreme = current.low
        else:
            swept = current.high > outer
            reclaimed = current.close <= inner
            outside_close = current.close > outer
            extreme = current.high

        if swept and reclaimed and self.config.enable_immediate_fakeout:
            state.extreme = extreme
            state.completed = True
            self._count("immediate_pool_fakeouts")
            return self._build_setup(state, current, "IMMEDIATE_FAKEOUT_RETEST")
        if swept and outside_close:
            state.outside = True
            state.outside_first_index = index
            state.extreme = extreme
            self._count("pool_outside_closes")
        return None

    def on_candle(self, candle: Candle, index: int) -> list[ArmedSetup]:
        update = self.detector.on_candle(candle, index)
        for pool in update.formed:
            self.states[pool.pool_id] = PoolInteractionState(pool)
        for pool in update.mitigated:
            self.states.pop(pool.pool_id, None)
            self._count("interaction_state_mitigated")

        setups: list[ArmedSetup] = []
        for pool_id, state in list(self.states.items()):
            setup = self._observe_pool(state, candle, index)
            if setup is not None:
                setups.append(setup)
            if state.completed:
                self.states.pop(pool_id, None)
        return sorted(
            setups,
            key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id),
        )

    def combined_diagnostics(self) -> dict[str, int]:
        output = dict(self.diagnostics)
        for key, value in self.detector.diagnostics.items():
            output[f"detector_{key}"] = value
        return output


__all__ = [
    "EasyChartLiquidityPoolEngine",
    "LiquidityPool",
    "PoolDetectorConfig",
    "PoolDetectorUpdate",
    "PoolInteractionState",
    "PoolTrapConfig",
    "WickLiquidityPoolDetector",
]
