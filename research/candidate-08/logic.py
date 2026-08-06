"""Causal SMC/ICT liquidity-state logic for candidate-08.

This module deliberately contains no backtest or accounting implementation.  It is a
pattern detector plus scenario state machine.  NautilusTrader remains responsible for
market replay, orders, fills, positions, fees, margin, liquidation, and account state.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from statistics import median
from typing import Any, Iterable


class PoolKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ScenarioFamily(str, Enum):
    REJECTION = "REJECTION"
    ACCEPTANCE = "ACCEPTANCE"


@dataclass(frozen=True, slots=True)
class BarPoint:
    index: int
    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(isfinite(value) for value in values):
            raise ValueError("bar values must be finite")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC ordering")
        if self.low > self.high or self.volume < 0:
            raise ValueError("invalid bar range or volume")

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.close - self.low) / self.range


@dataclass(slots=True)
class LiquidityPool:
    pool_id: str
    kind: PoolKind
    level: float
    pivot_index: int
    event_time_ns: int
    observed_time_ns: int
    touches: int = 1
    last_touch_index: int = 0
    consumed: bool = False


@dataclass(slots=True)
class PendingScenario:
    scenario_id: str
    family: ScenarioFamily
    direction: Direction
    pool_id: str
    pool_level: float
    armed_index: int
    expiry_index: int
    atr: float
    extreme: float
    confirmation_level: float
    reference_range: float
    interaction_time_ns: int
    interaction_volume_ratio: float = 1.0
    pool_age_bars: int = 0
    pool_touches: int = 1
    retest_index: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    retest_volume_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class TradeSetup:
    scenario_id: str
    family: ScenarioFamily
    direction: Direction
    signal_index: int
    signal_time_ns: int
    pool_id: str
    pool_level: float
    estimated_entry: float
    structural_stop: float
    liquidity_target: float
    atr: float
    reason_code: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogicEvent:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogicConfig:
    atr_period: int = 20
    volume_period: int = 30
    swing_left: int = 3
    swing_right: int = 3
    pool_merge_atr: float = 0.12
    pool_max_age_bars: int = 1440
    minimum_pool_touches: int = 1
    minimum_atr_fraction: float = 0.00035
    minimum_sweep_atr: float = 0.04
    maximum_sweep_atr: float = 1.8
    reclaim_atr: float = 0.02
    minimum_rejection_wick_body: float = 0.55
    minimum_interaction_range_atr: float = 0.55
    minimum_volume_ratio: float = 0.75
    rejection_confirmation_bars: int = 3
    rejection_confirmation_atr: float = 0.08
    acceptance_close_atr: float = 0.12
    acceptance_body_atr: float = 0.50
    acceptance_close_location: float = 0.68
    acceptance_volume_ratio: float = 0.95
    acceptance_retest_bars: int = 10
    minimum_pool_visibility_bars: int = 30
    acceptance_retest_volume_fraction: float = 0.75
    acceptance_follow_through_bars: int = 3
    acceptance_follow_through_atr: float = 0.05
    acceptance_follow_through_body_atr: float = 0.25
    acceptance_follow_through_close_location: float = 0.65
    retest_outer_atr: float = 0.22
    retest_inner_atr: float = 0.38
    retest_close_atr: float = 0.01
    stop_buffer_atr: float = 0.10
    minimum_stop_atr: float = 0.18
    minimum_net_reward_risk: float = 1.20
    projection_fraction: float = 0.786
    maximum_hold_bars: int = 180
    funding_avoidance_minutes: int = 185
    cooldown_bars: int = 3

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "LogicConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})


class LiquidityBifurcationLogic:
    """Detect confirmed liquidity pools, then classify rejection vs acceptance.

    A swing's ``event_time_ns`` is its visual pivot time.  It is not inserted into
    the active pool set until ``swing_right`` later bars have closed; the associated
    ``observed_time_ns`` is therefore later and is emitted explicitly.
    """

    def __init__(self, config: LogicConfig):
        self.config = config
        history_size = max(
            config.atr_period + 3,
            config.volume_period + 3,
            config.swing_left + config.swing_right + 5,
        )
        self.bars: deque[BarPoint] = deque(maxlen=max(history_size, 256))
        self.true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self.volumes: deque[float] = deque(maxlen=config.volume_period)
        self.pools: list[LiquidityPool] = []
        self.pending: PendingScenario | None = None
        self.events: list[LogicEvent] = []
        self.last_trade_index = -10**9
        self._pool_counter = 0
        self._scenario_counter = 0
        self._last_close: float | None = None

    @property
    def ready(self) -> bool:
        return (
            len(self.true_ranges) >= self.config.atr_period
            and len(self.volumes) >= self.config.volume_period
            and len(self.bars) >= self.config.swing_left + self.config.swing_right + 1
        )

    @property
    def atr(self) -> float | None:
        if len(self.true_ranges) < self.config.atr_period:
            return None
        return sum(self.true_ranges) / len(self.true_ranges)

    @property
    def volume_median(self) -> float | None:
        if len(self.volumes) < self.config.volume_period:
            return None
        return float(median(self.volumes))

    def mark_trade(self, signal_index: int) -> None:
        self.last_trade_index = signal_index
        self.pending = None

    def clear_pending(self, reason_code: str, bar: BarPoint) -> None:
        if self.pending is None:
            return
        self._emit(
            scenario_id=self.pending.scenario_id,
            event_type="SCENARIO_CANCELLED",
            event_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            previous_state="ARMED",
            next_state="CANCELLED",
            reason_code=reason_code,
            reference_price=bar.close,
        )
        self.pending = None

    def on_bar(self, bar: BarPoint, *, trading_available: bool = True) -> list[TradeSetup]:
        previous_close = self._last_close if self._last_close is not None else bar.open
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        self.true_ranges.append(true_range)
        self.volumes.append(bar.volume)
        self.bars.append(bar)
        self._last_close = bar.close

        self._confirm_new_pivots(bar)
        self._prune_pools(bar.index)

        atr = self.atr
        volume_median = self.volume_median
        if atr is None or volume_median is None or atr <= 0:
            return []
        if atr / max(bar.close, 1e-12) < self.config.minimum_atr_fraction:
            self.clear_pending("INSUFFICIENT_COST_ADJUSTED_RANGE", bar)
            return []

        setup = self._advance_pending(bar, atr, volume_median, trading_available)
        if setup is not None:
            return [setup]

        if not trading_available:
            return []
        if bar.index - self.last_trade_index <= self.config.cooldown_bars:
            return []
        if self.pending is None:
            self._detect_new_interaction(bar, previous_close, atr, volume_median)
        return []

    def _confirm_new_pivots(self, observed_bar: BarPoint) -> None:
        required = self.config.swing_left + self.config.swing_right + 1
        if len(self.bars) < required:
            return
        bars = list(self.bars)
        candidate_pos = len(bars) - 1 - self.config.swing_right
        if candidate_pos < self.config.swing_left:
            return
        candidate = bars[candidate_pos]
        window = bars[
            candidate_pos - self.config.swing_left : candidate_pos + self.config.swing_right + 1
        ]
        highs = [item.high for item in window]
        lows = [item.low for item in window]
        if candidate.high == max(highs) and highs.count(candidate.high) == 1:
            self._register_pool(PoolKind.HIGH, candidate, observed_bar)
        if candidate.low == min(lows) and lows.count(candidate.low) == 1:
            self._register_pool(PoolKind.LOW, candidate, observed_bar)

    def _register_pool(self, kind: PoolKind, pivot: BarPoint, observed: BarPoint) -> None:
        atr = self.atr or max(pivot.range, 1e-12)
        level = pivot.high if kind is PoolKind.HIGH else pivot.low
        merge_distance = self.config.pool_merge_atr * atr
        eligible = [
            pool
            for pool in self.pools
            if not pool.consumed and pool.kind is kind and abs(pool.level - level) <= merge_distance
        ]
        if eligible:
            pool = min(eligible, key=lambda item: abs(item.level - level))
            pool.level = (pool.level * pool.touches + level) / (pool.touches + 1)
            pool.touches += 1
            pool.last_touch_index = observed.index
            self._emit(
                scenario_id=pool.pool_id,
                event_type="LIQUIDITY_POOL_REINFORCED",
                event_time_ns=pivot.ts_event_ns,
                observed_time_ns=observed.ts_event_ns,
                previous_state="CONFIRMED",
                next_state="CONFIRMED",
                reason_code=f"{kind.value}_SWING_CLUSTER",
                reference_price=pool.level,
                details={"touches": pool.touches},
            )
            return

        self._pool_counter += 1
        pool = LiquidityPool(
            pool_id=f"pool-{kind.value.lower()}-{self._pool_counter:06d}",
            kind=kind,
            level=level,
            pivot_index=pivot.index,
            event_time_ns=pivot.ts_event_ns,
            observed_time_ns=observed.ts_event_ns,
            touches=1,
            last_touch_index=observed.index,
        )
        self.pools.append(pool)
        self._emit(
            scenario_id=pool.pool_id,
            event_type="LIQUIDITY_POOL_CONFIRMED",
            event_time_ns=pivot.ts_event_ns,
            observed_time_ns=observed.ts_event_ns,
            previous_state="UNSEEN",
            next_state="CONFIRMED",
            reason_code=f"CAUSAL_{kind.value}_SWING",
            reference_price=level,
            details={
                "pivot_index": pivot.index,
                "observed_index": observed.index,
                "right_confirmation_bars": self.config.swing_right,
            },
        )

    def _prune_pools(self, current_index: int) -> None:
        for pool in self.pools:
            if not pool.consumed and current_index - pool.pivot_index > self.config.pool_max_age_bars:
                pool.consumed = True

    def _detect_new_interaction(
        self,
        bar: BarPoint,
        previous_close: float,
        atr: float,
        volume_median: float,
    ) -> None:
        active = [
            pool
            for pool in self.pools
            if not pool.consumed
            and pool.touches >= self.config.minimum_pool_touches
            and (
                bar.index - pool.pivot_index >= self.config.minimum_pool_visibility_bars
                or pool.touches >= 2
            )
        ]
        high_crossed = [
            pool
            for pool in active
            if pool.kind is PoolKind.HIGH
            and previous_close <= pool.level
            and bar.high >= pool.level + self.config.minimum_sweep_atr * atr
        ]
        low_crossed = [
            pool
            for pool in active
            if pool.kind is PoolKind.LOW
            and previous_close >= pool.level
            and bar.low <= pool.level - self.config.minimum_sweep_atr * atr
        ]

        if high_crossed and low_crossed:
            self._emit(
                scenario_id=f"unresolved-{bar.index}",
                event_type="BILATERAL_LIQUIDITY_EXPANSION",
                event_time_ns=bar.ts_event_ns,
                observed_time_ns=bar.ts_event_ns,
                previous_state="IDLE",
                next_state="UNRESOLVED",
                reason_code="BOTH_SIDES_INTERACTED_SAME_BAR",
                reference_price=bar.close,
            )
            return

        if high_crossed:
            pool = max(high_crossed, key=lambda item: item.level)
            self._classify_interaction(pool, bar, atr, volume_median)
        elif low_crossed:
            pool = min(low_crossed, key=lambda item: item.level)
            self._classify_interaction(pool, bar, atr, volume_median)

    def _classify_interaction(
        self,
        pool: LiquidityPool,
        bar: BarPoint,
        atr: float,
        volume_median: float,
    ) -> None:
        penetration = (
            bar.high - pool.level if pool.kind is PoolKind.HIGH else pool.level - bar.low
        )
        penetration_atr = penetration / atr
        if penetration_atr > self.config.maximum_sweep_atr:
            pool.consumed = True
            return
        volume_ratio = bar.volume / max(volume_median, 1e-12)
        range_atr = bar.range / atr
        body = max(bar.body, atr * 0.01)

        if pool.kind is PoolKind.HIGH:
            wick = bar.high - max(bar.open, bar.close)
            rejected = (
                bar.close <= pool.level - self.config.reclaim_atr * atr
                and wick / body >= self.config.minimum_rejection_wick_body
                and range_atr >= self.config.minimum_interaction_range_atr
                and volume_ratio >= self.config.minimum_volume_ratio
            )
            accepted = (
                bar.close >= pool.level + self.config.acceptance_close_atr * atr
                and bar.body >= self.config.acceptance_body_atr * atr
                and bar.close_location >= self.config.acceptance_close_location
                and volume_ratio >= self.config.acceptance_volume_ratio
            )
            direction = Direction.SHORT if rejected else Direction.LONG
        else:
            wick = min(bar.open, bar.close) - bar.low
            rejected = (
                bar.close >= pool.level + self.config.reclaim_atr * atr
                and wick / body >= self.config.minimum_rejection_wick_body
                and range_atr >= self.config.minimum_interaction_range_atr
                and volume_ratio >= self.config.minimum_volume_ratio
            )
            accepted = (
                bar.close <= pool.level - self.config.acceptance_close_atr * atr
                and bar.body >= self.config.acceptance_body_atr * atr
                and bar.close_location <= 1.0 - self.config.acceptance_close_location
                and volume_ratio >= self.config.acceptance_volume_ratio
            )
            direction = Direction.LONG if rejected else Direction.SHORT

        if rejected:
            family = ScenarioFamily.REJECTION
            expiry = bar.index + self.config.rejection_confirmation_bars
            confirmation_level = (bar.high + bar.low) / 2.0
        elif accepted:
            family = ScenarioFamily.ACCEPTANCE
            expiry = bar.index + self.config.acceptance_retest_bars
            confirmation_level = pool.level
        else:
            return

        pool.consumed = True
        self._scenario_counter += 1
        scenario_id = f"lsb-{self._scenario_counter:06d}"
        self.pending = PendingScenario(
            scenario_id=scenario_id,
            family=family,
            direction=direction,
            pool_id=pool.pool_id,
            pool_level=pool.level,
            armed_index=bar.index,
            expiry_index=expiry,
            atr=atr,
            extreme=bar.low if direction is Direction.LONG else bar.high,
            confirmation_level=confirmation_level,
            reference_range=self._reference_range(pool, bar, atr),
            interaction_time_ns=bar.ts_event_ns,
            interaction_volume_ratio=volume_ratio,
            pool_age_bars=bar.index - pool.pivot_index,
            pool_touches=pool.touches,
        )
        self._emit(
            scenario_id=pool.pool_id,
            event_type="LIQUIDITY_POOL_CONSUMED",
            event_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            previous_state="CONFIRMED",
            next_state="CONSUMED",
            reason_code=f"{family.value}_INTERACTION",
            reference_price=pool.level,
        )
        self._emit(
            scenario_id=scenario_id,
            event_type="SCENARIO_ARMED",
            event_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            previous_state="IDLE",
            next_state="ARMED",
            reason_code=f"{family.value}_{direction.value}",
            reference_price=pool.level,
            details={
                "pool_id": pool.pool_id,
                "penetration_atr": penetration_atr,
                "volume_ratio": volume_ratio,
                "range_atr": range_atr,
                "interaction_volume_ratio": volume_ratio,
                "pool_age_bars": bar.index - pool.pivot_index,
                "pool_touches": pool.touches,
                "expiry_index": expiry,
            },
        )

    def _advance_pending(
        self,
        bar: BarPoint,
        atr: float,
        volume_median: float,
        trading_available: bool,
    ) -> TradeSetup | None:
        pending = self.pending
        if pending is None:
            return None
        if bar.index <= pending.armed_index:
            return None
        if bar.index > pending.expiry_index:
            self.clear_pending("SCENARIO_TIMEOUT", bar)
            return None

        volume_ratio = bar.volume / max(volume_median, 1e-12)
        if pending.family is ScenarioFamily.REJECTION:
            if pending.direction is Direction.LONG:
                invalid = bar.close < pending.extreme - self.config.stop_buffer_atr * atr
                confirmed = (
                    bar.close >= pending.confirmation_level + self.config.rejection_confirmation_atr * atr
                    and bar.close > bar.open
                    and bar.close_location >= 0.58
                )
            else:
                invalid = bar.close > pending.extreme + self.config.stop_buffer_atr * atr
                confirmed = (
                    bar.close <= pending.confirmation_level - self.config.rejection_confirmation_atr * atr
                    and bar.close < bar.open
                    and bar.close_location <= 0.42
                )
        else:
            if pending.direction is Direction.LONG:
                invalid = bar.close < pending.pool_level - self.config.retest_inner_atr * atr
            else:
                invalid = bar.close > pending.pool_level + self.config.retest_inner_atr * atr

            if invalid:
                confirmed = False
            elif pending.retest_index is None:
                if pending.direction is Direction.LONG:
                    touched = bar.low <= pending.pool_level + self.config.retest_outer_atr * atr
                    held = (
                        bar.close >= pending.pool_level + self.config.retest_close_atr * atr
                        and bar.close_location >= 0.55
                    )
                    if touched:
                        pending.extreme = min(pending.extreme, bar.low)
                else:
                    touched = bar.high >= pending.pool_level - self.config.retest_outer_atr * atr
                    held = (
                        bar.close <= pending.pool_level - self.config.retest_close_atr * atr
                        and bar.close_location <= 0.45
                    )
                    if touched:
                        pending.extreme = max(pending.extreme, bar.high)
                confirmed = False
                if touched and held:
                    contraction_limit = (
                        pending.interaction_volume_ratio
                        * self.config.acceptance_retest_volume_fraction
                    )
                    if volume_ratio > contraction_limit:
                        self.clear_pending("ACCEPTANCE_RETEST_NOT_CONTRACTED", bar)
                        return None
                    pending.retest_index = bar.index
                    pending.retest_high = bar.high
                    pending.retest_low = bar.low
                    pending.retest_volume_ratio = volume_ratio
                    pending.expiry_index = (
                        bar.index + self.config.acceptance_follow_through_bars
                    )
                    self._emit(
                        scenario_id=pending.scenario_id,
                        event_type="ACCEPTANCE_RETEST_HELD",
                        event_time_ns=bar.ts_event_ns,
                        observed_time_ns=bar.ts_event_ns,
                        previous_state="ARMED",
                        next_state="RETEST_HELD",
                        reason_code=f"LOW_ENERGY_RETEST_{pending.direction.value}",
                        reference_price=bar.close,
                        details={
                            "interaction_volume_ratio": pending.interaction_volume_ratio,
                            "retest_volume_ratio": volume_ratio,
                            "contraction_fraction": (
                                volume_ratio / max(pending.interaction_volume_ratio, 1e-12)
                            ),
                            "follow_through_expiry_index": pending.expiry_index,
                        },
                    )
                    return None
            elif pending.direction is Direction.LONG:
                assert pending.retest_high is not None
                assert pending.retest_volume_ratio is not None
                confirmed = (
                    bar.close
                    >= pending.retest_high + self.config.acceptance_follow_through_atr * atr
                    and bar.close > bar.open
                    and bar.body >= self.config.acceptance_follow_through_body_atr * atr
                    and bar.close_location
                    >= self.config.acceptance_follow_through_close_location
                    and volume_ratio >= pending.retest_volume_ratio
                )
            else:
                assert pending.retest_low is not None
                assert pending.retest_volume_ratio is not None
                confirmed = (
                    bar.close
                    <= pending.retest_low - self.config.acceptance_follow_through_atr * atr
                    and bar.close < bar.open
                    and bar.body >= self.config.acceptance_follow_through_body_atr * atr
                    and bar.close_location
                    <= 1.0 - self.config.acceptance_follow_through_close_location
                    and volume_ratio >= pending.retest_volume_ratio
                )

        if invalid:
            self.clear_pending("STRUCTURE_INVALIDATED_BEFORE_ENTRY", bar)
            return None
        if not confirmed or not trading_available:
            return None

        entry = bar.close
        stop = self._structural_stop(pending, entry, atr)
        target = self._liquidity_target(pending, entry, stop, atr)
        if target is None:
            self.clear_pending("NO_CAUSAL_LIQUIDITY_TARGET", bar)
            return None

        setup = TradeSetup(
            scenario_id=pending.scenario_id,
            family=pending.family,
            direction=pending.direction,
            signal_index=bar.index,
            signal_time_ns=bar.ts_event_ns,
            pool_id=pending.pool_id,
            pool_level=pending.pool_level,
            estimated_entry=entry,
            structural_stop=stop,
            liquidity_target=target,
            atr=atr,
            reason_code=f"{pending.family.value}_{pending.direction.value}_CONFIRMED",
            details={
                "volume_ratio": volume_ratio,
                "armed_index": pending.armed_index,
                "confirmation_index": bar.index,
                "reference_range": pending.reference_range,
                "interaction_volume_ratio": pending.interaction_volume_ratio,
                "retest_volume_ratio": pending.retest_volume_ratio,
                "pool_age_bars": pending.pool_age_bars,
                "pool_touches": pending.pool_touches,
                "retest_index": pending.retest_index,
            },
        )
        self._emit(
            scenario_id=pending.scenario_id,
            event_type="SCENARIO_CONFIRMED",
            event_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            previous_state="ARMED",
            next_state="CONFIRMED",
            reason_code=setup.reason_code,
            reference_price=entry,
            details={"stop": stop, "target": target},
        )
        return setup

    def _structural_stop(self, pending: PendingScenario, entry: float, atr: float) -> float:
        minimum_distance = self.config.minimum_stop_atr * atr
        buffer_distance = self.config.stop_buffer_atr * atr
        if pending.direction is Direction.LONG:
            structural = pending.extreme - buffer_distance
            return min(structural, entry - minimum_distance)
        structural = pending.extreme + buffer_distance
        return max(structural, entry + minimum_distance)

    def _liquidity_target(
        self,
        pending: PendingScenario,
        entry: float,
        stop: float,
        atr: float,
    ) -> float | None:
        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            return None
        if pending.direction is Direction.LONG:
            candidates = sorted(
                pool.level
                for pool in self.pools
                if not pool.consumed and pool.kind is PoolKind.HIGH and pool.level > entry
            )
            for level in candidates:
                if level - entry >= self.config.minimum_net_reward_risk * stop_distance:
                    return level
            projected = pending.pool_level + self.config.projection_fraction * pending.reference_range
            minimum = entry + self.config.minimum_net_reward_risk * stop_distance
            return max(projected, minimum) if projected > entry else minimum

        candidates = sorted(
            (
                pool.level
                for pool in self.pools
                if not pool.consumed and pool.kind is PoolKind.LOW and pool.level < entry
            ),
            reverse=True,
        )
        for level in candidates:
            if entry - level >= self.config.minimum_net_reward_risk * stop_distance:
                return level
        projected = pending.pool_level - self.config.projection_fraction * pending.reference_range
        maximum = entry - self.config.minimum_net_reward_risk * stop_distance
        return min(projected, maximum) if projected < entry else maximum

    def _reference_range(self, pool: LiquidityPool, bar: BarPoint, atr: float) -> float:
        opposite = [
            item
            for item in self.pools
            if not item.consumed
            and item.kind is not pool.kind
            and item.pivot_index < bar.index
        ]
        if pool.kind is PoolKind.HIGH:
            below = [item for item in opposite if item.level < pool.level]
            if below:
                return max(pool.level - max(below, key=lambda item: item.pivot_index).level, atr)
        else:
            above = [item for item in opposite if item.level > pool.level]
            if above:
                return max(max(above, key=lambda item: item.pivot_index).level - pool.level, atr)
        return max(bar.range, atr)

    def _emit(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            LogicEvent(
                scenario_id=scenario_id,
                event_type=event_type,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=reference_price,
                details=dict(details or {}),
            )
        )


def minutes_to_next_funding(ts_event_ns: int) -> int:
    """Return whole minutes to the next 00:00/08:00/16:00 UTC funding boundary."""

    seconds = ts_event_ns // 1_000_000_000
    minute_of_day = (seconds // 60) % (24 * 60)
    boundaries = (0, 8 * 60, 16 * 60, 24 * 60)
    for boundary in boundaries:
        if boundary > minute_of_day:
            return boundary - minute_of_day
    return 24 * 60 - minute_of_day


def net_reward_risk(
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    fee_rate: float,
    tick_size: float,
) -> tuple[float, float, float]:
    """Return expected per-unit stop loss, target gain, and net gain/loss ratio."""

    stop_loss = abs(entry - stop) + fee_rate * (entry + stop) + 2.0 * tick_size
    gross_gain = (target - entry) if direction is Direction.LONG else (entry - target)
    target_gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick_size
    ratio = target_gain / stop_loss if stop_loss > 0 else float("-inf")
    return stop_loss, target_gain, ratio


def floor_to_increment(value: float, increment: float) -> float:
    if value <= 0 or increment <= 0:
        return 0.0
    units = int(value / increment + 1e-12)
    return units * increment


def risk_sized_quantity(
    *,
    nav: float,
    risk_fraction: float,
    expected_loss_per_unit: float,
    size_increment: float,
) -> tuple[float, float]:
    """Return floor-rounded quantity and resulting planned loss budget usage."""

    if nav <= 0 or not 0 < risk_fraction <= 0.03:
        raise ValueError("risk sizing requires positive NAV and risk_fraction <= 3%")
    if expected_loss_per_unit <= 0:
        raise ValueError("expected loss per unit must be positive")
    budget = nav * risk_fraction
    quantity = floor_to_increment(budget / expected_loss_per_unit, size_increment)
    return quantity, quantity * expected_loss_per_unit


def group_events_by_reason(events: Iterable[LogicEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.reason_code] = counts.get(event.reason_code, 0) + 1
    return counts
