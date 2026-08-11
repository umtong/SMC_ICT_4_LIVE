"""Time-structured liquidity traps for candidate-easychart v7.

The source material explicitly says that a human trader does not act on every
OB or every local pivot.  The trader first identifies a visibly accumulated
liquidity range and asks whether it was deliberately broken near a market open
or close.  The wider ICT material supplied with the project also names Asian,
London and New York sessions, previous-day highs/lows and kill zones.

v7 turns that implicit time-and-price hierarchy into one minimal policy:

reference session range -> first one-sided sweep -> immediate Fake out or
later Trap reclaim -> first retest of the reclaimed boundary -> opposite side
of the same range.

The direct reclaim/retest entry is stated in the Fake out/Trap PDF, so this
family deliberately does not add an OB/BOS/FVG requirement.  Those footprint
certificates remain separate families rather than redundant safety filters.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from domain_v3 import ArmedSetup, Candle, Side, TargetMode


@dataclass(frozen=True, slots=True)
class SessionLiquidityRange:
    range_id: str
    reference_family: str
    trade_window: str
    observed_time_ns: int
    trade_start_ns: int
    trade_end_ns: int
    high: float
    low: float

    def __post_init__(self) -> None:
        if not self.range_id or not self.reference_family or not self.trade_window:
            raise ValueError("session range identifiers must be non-empty")
        if not all(math.isfinite(value) for value in (self.high, self.low)):
            raise ValueError("session range prices must be finite")
        if self.high <= self.low:
            raise ValueError("session range high must exceed low")
        if not self.observed_time_ns <= self.trade_start_ns < self.trade_end_ns:
            raise ValueError("invalid session range timing")

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class ExpiringArmedSetup(ArmedSetup):
    valid_until_ns: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.valid_until_ns <= self.observed_time_ns:
            raise ValueError("expiring setup must remain valid after observation")


@dataclass(slots=True)
class SessionRangeState:
    liquidity_range: SessionLiquidityRange
    outside_side: Side | None = None
    outside_first_index: int | None = None
    outside_extreme: float | None = None
    completed: bool = False


@dataclass(frozen=True, slots=True)
class SessionTrapConfig:
    enable_immediate_fakeout: bool = True
    enable_delayed_trap: bool = True
    accepted_break_range_widths: float = 1.0
    tick_size: float = 0.1
    source_timeframe_minutes: int = 5

    def __post_init__(self) -> None:
        if not (self.enable_immediate_fakeout or self.enable_delayed_trap):
            raise ValueError("at least one interaction family must be enabled")
        if not math.isfinite(self.accepted_break_range_widths) or self.accepted_break_range_widths <= 0.0:
            raise ValueError("accepted_break_range_widths must be positive")
        if not math.isfinite(self.tick_size) or self.tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        if self.source_timeframe_minutes <= 0:
            raise ValueError("source_timeframe_minutes must be positive")


class EasyChartSessionTrapEngine:
    """Causal multi-range session trap state machine for one symbol."""

    def __init__(
        self,
        symbol: str,
        ranges: Iterable[SessionLiquidityRange],
        config: SessionTrapConfig,
    ) -> None:
        self.symbol = symbol
        self.config = config
        ordered = sorted(
            ranges,
            key=lambda item: (item.trade_start_ns, item.trade_end_ns, item.range_id),
        )
        self.pending_ranges = ordered
        self.range_cursor = 0
        self.active: dict[str, SessionRangeState] = {}
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _count(self, key: str, amount: int = 1) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + amount

    def _new_id(self, prefix: str) -> str:
        self.sequence += 1
        return f"ec7-{self.symbol}-{prefix}-{self.sequence:08d}"

    def _activate_and_expire(self, current: Candle) -> None:
        while (
            self.range_cursor < len(self.pending_ranges)
            and self.pending_ranges[self.range_cursor].trade_start_ns <= current.ts_open_ns
        ):
            liquidity_range = self.pending_ranges[self.range_cursor]
            self.range_cursor += 1
            if liquidity_range.trade_end_ns <= current.ts_open_ns:
                self._count("range_missed_before_activation")
                continue
            self.active[liquidity_range.range_id] = SessionRangeState(liquidity_range)
            self._count("ranges_activated")
            self._count(f"ranges_activated_{liquidity_range.reference_family}_{liquidity_range.trade_window}")

        for range_id, state in list(self.active.items()):
            if state.liquidity_range.trade_end_ns <= current.ts_open_ns:
                self.active.pop(range_id, None)
                if not state.completed:
                    self._count("range_window_expired")

    @staticmethod
    def _accepted_break(
        liquidity_range: SessionLiquidityRange,
        side: Side,
        close: float,
        multiple: float,
    ) -> bool:
        distance = liquidity_range.width * multiple
        if side is Side.LONG:
            return close <= liquidity_range.low - distance
        return close >= liquidity_range.high + distance

    def _build_setup(
        self,
        state: SessionRangeState,
        current: Candle,
        side: Side,
        extreme: float,
        interaction: str,
    ) -> ExpiringArmedSetup | None:
        liquidity_range = state.liquidity_range
        if side is Side.LONG:
            entry = liquidity_range.low
            stop = extreme - self.config.tick_size
            target = liquidity_range.high
            if current.high >= target:
                self._count("target_consumed_on_reclaim")
                return None
        else:
            entry = liquidity_range.high
            stop = extreme + self.config.tick_size
            target = liquidity_range.low
            if current.low <= target:
                self._count("target_consumed_on_reclaim")
                return None
        if side is Side.LONG and not stop < entry < target:
            self._count("invalid_long_geometry")
            return None
        if side is Side.SHORT and not target < entry < stop:
            self._count("invalid_short_geometry")
            return None

        family = (
            f"SESSION_{liquidity_range.reference_family}_"
            f"{liquidity_range.trade_window}_{interaction}"
        )
        setup = ExpiringArmedSetup(
            setup_id=self._new_id("setup"),
            causal_event_id=(
                f"{family}:{self.symbol}:{liquidity_range.range_id}:"
                f"{current.ts_close_ns}:{side.name}"
            ),
            symbol=self.symbol,
            family=family,
            side=side,
            observed_time_ns=current.ts_close_ns,
            entry=entry,
            stop=stop,
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=target,
            fixed_target_id=f"OPPOSITE_BOUNDARY:{liquidity_range.range_id}",
            source_pool_id=liquidity_range.range_id,
            zone_low=entry,
            zone_high=entry,
            formation_extreme=extreme,
            body_ratio=0.0,
            previous_body=0.0,
            current_body=0.0,
            context_bias=f"{liquidity_range.reference_family}->{liquidity_range.trade_window}",
            source_timeframe_minutes=self.config.source_timeframe_minutes,
            valid_until_ns=liquidity_range.trade_end_ns,
        )
        plan = setup.executable(target, target_id=setup.fixed_target_id, min_gross_rr=1.0)
        if plan is None:
            self._count("gross_rr_lt_1")
            return None
        self._count("setups_armed")
        self._count(f"setups_armed_{family}")
        return setup

    def _observe_state(
        self,
        state: SessionRangeState,
        current: Candle,
        index: int,
    ) -> ExpiringArmedSetup | None:
        liquidity_range = state.liquidity_range
        if state.completed:
            return None
        if not (
            liquidity_range.trade_start_ns <= current.ts_open_ns
            and current.ts_open_ns < liquidity_range.trade_end_ns
        ):
            return None

        lower_cross = current.low < liquidity_range.low
        upper_cross = current.high > liquidity_range.high
        if lower_cross and upper_cross:
            state.completed = True
            self._count("two_sided_same_bar_ambiguous")
            return None

        if state.outside_side is not None:
            side = state.outside_side
            assert state.outside_extreme is not None
            if side is Side.LONG:
                state.outside_extreme = min(state.outside_extreme, current.low)
                reclaimed = current.close >= liquidity_range.low
            else:
                state.outside_extreme = max(state.outside_extreme, current.high)
                reclaimed = current.close <= liquidity_range.high

            if reclaimed and self.config.enable_delayed_trap:
                duration = index - int(state.outside_first_index or index)
                self._count("delayed_trap_reclaims")
                self._count(f"delayed_trap_duration_{min(duration, 60)}")
                state.completed = True
                return self._build_setup(
                    state,
                    current,
                    side,
                    state.outside_extreme,
                    "DELAYED_TRAP_RETEST",
                )
            if self._accepted_break(
                liquidity_range,
                side,
                current.close,
                self.config.accepted_break_range_widths,
            ):
                state.completed = True
                self._count("accepted_break_full_range")
            return None

        if lower_cross:
            if current.close >= liquidity_range.low and self.config.enable_immediate_fakeout:
                state.completed = True
                self._count("immediate_fakeouts")
                return self._build_setup(
                    state,
                    current,
                    Side.LONG,
                    current.low,
                    "IMMEDIATE_FAKEOUT_RETEST",
                )
            if current.close < liquidity_range.low:
                if self._accepted_break(
                    liquidity_range,
                    Side.LONG,
                    current.close,
                    self.config.accepted_break_range_widths,
                ):
                    state.completed = True
                    self._count("accepted_break_full_range")
                else:
                    state.outside_side = Side.LONG
                    state.outside_first_index = index
                    state.outside_extreme = current.low
                    self._count("outside_closes")
            return None

        if upper_cross:
            if current.close <= liquidity_range.high and self.config.enable_immediate_fakeout:
                state.completed = True
                self._count("immediate_fakeouts")
                return self._build_setup(
                    state,
                    current,
                    Side.SHORT,
                    current.high,
                    "IMMEDIATE_FAKEOUT_RETEST",
                )
            if current.close > liquidity_range.high:
                if self._accepted_break(
                    liquidity_range,
                    Side.SHORT,
                    current.close,
                    self.config.accepted_break_range_widths,
                ):
                    state.completed = True
                    self._count("accepted_break_full_range")
                else:
                    state.outside_side = Side.SHORT
                    state.outside_first_index = index
                    state.outside_extreme = current.high
                    self._count("outside_closes")
        return None

    def on_close(self, current: Candle, index: int) -> list[ExpiringArmedSetup]:
        self._activate_and_expire(current)
        setups: list[ExpiringArmedSetup] = []
        for range_id, state in list(self.active.items()):
            setup = self._observe_state(state, current, index)
            if setup is not None:
                setups.append(setup)
            if state.completed:
                self.active.pop(range_id, None)
        return sorted(
            setups,
            key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id),
        )


__all__ = [
    "EasyChartSessionTrapEngine",
    "ExpiringArmedSetup",
    "SessionLiquidityRange",
    "SessionRangeState",
    "SessionTrapConfig",
]
