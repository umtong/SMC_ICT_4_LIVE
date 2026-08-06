"""Causal quarter-hour order-flow continuation logic.

The module detects a periodic participation burst, a contracted retrace, and a separate
reacceleration. It deliberately contains no exchange, fill, accounting, or position-sizing code;
NautilusTrader owns those responsibilities in the strategy adapter and runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any, Iterable


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SequenceState(str, Enum):
    IDLE = "IDLE"
    BURST_ARMED = "BURST_ARMED"
    RETRACE_HELD = "RETRACE_HELD"


@dataclass(frozen=True, slots=True)
class FlowBar:
    index: int
    ts_event_ns: int
    minute: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: float
    imbalance: float
    atr: float
    volume_ratio: float
    trade_ratio: float
    lag_mean4: float
    previous_session_high: float
    previous_session_low: float
    previous_session_direction: float
    efficiency_60m: float
    direction_60m: float

    def __post_init__(self) -> None:
        finite_values = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.trade_count,
            self.imbalance,
            self.atr,
            self.volume_ratio,
            self.trade_ratio,
            self.previous_session_high,
            self.previous_session_low,
        )
        if not all(isfinite(value) for value in finite_values):
            raise ValueError("required flow-bar values must be finite")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC ordering")
        if self.low > self.high or self.volume < 0 or self.trade_count < 0 or self.atr <= 0:
            raise ValueError("invalid range, activity, or ATR")

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def close_location(self) -> float:
        spread = self.high - self.low
        return (self.close - self.low) / spread if spread > 0 else 0.5


@dataclass(frozen=True, slots=True)
class QHLogicConfig:
    top_of_hour_only: bool = False
    regime_mode: str = "EFFICIENCY_60M"
    minimum_abs_imbalance: float = 0.18
    minimum_volume_ratio: float = 1.50
    minimum_trade_ratio: float = 1.25
    minimum_body_atr: float = 0.50
    minimum_lag_abs: float = 0.0
    minimum_efficiency_60m: float = 0.25
    minimum_retrace_fraction: float = 0.25
    maximum_origin_close_violation_atr: float = 0.10
    maximum_origin_extreme_violation_atr: float = 0.35
    maximum_retrace_volume_fraction: float = 0.80
    maximum_retrace_trade_fraction: float = 0.90
    maximum_retrace_imbalance_fraction: float = 0.75
    retrace_imbalance_allowance: float = 0.03
    maximum_retrace_bars: int = 6
    reacceleration_break_atr: float = 0.05
    minimum_reacceleration_body_atr: float = 0.15
    minimum_reacceleration_imbalance: float = 0.05
    reacceleration_volume_multiplier: float = 1.05
    maximum_reacceleration_bars: int = 4
    stop_buffer_atr: float = 0.05
    minimum_stop_atr: float = 0.25
    external_range_extension: float = 0.50
    cooldown_bars: int = 3

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "QHLogicConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})


@dataclass(slots=True)
class ArmedBurst:
    scenario_id: str
    direction: Direction
    burst_index: int
    burst_time_ns: int
    expiry_index: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: float
    imbalance: float
    atr: float
    previous_session_high: float
    previous_session_low: float
    previous_session_direction: float
    efficiency_60m: float
    direction_60m: float
    retrace_index: int | None = None
    retrace_time_ns: int | None = None
    retrace_high: float | None = None
    retrace_low: float | None = None
    retrace_volume: float | None = None
    retrace_trade_count: float | None = None
    retrace_imbalance: float | None = None


@dataclass(frozen=True, slots=True)
class QHSetup:
    scenario_id: str
    family: str
    direction: Direction
    signal_index: int
    signal_time_ns: int
    estimated_entry: float
    structural_stop: float
    external_target: float
    atr: float
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


class QuarterHourContinuationLogic:
    """State machine for burst -> contracted retrace -> independent reacceleration."""

    def __init__(self, config: QHLogicConfig):
        if config.regime_mode not in {"NONE", "PREVIOUS_SESSION", "EFFICIENCY_60M"}:
            raise ValueError(f"unsupported regime_mode: {config.regime_mode}")
        self.config = config
        self.pending: ArmedBurst | None = None
        self.events: list[LogicEvent] = []
        self.last_trade_index = -10**9
        self._scenario_counter = 0

    @property
    def state(self) -> SequenceState:
        if self.pending is None:
            return SequenceState.IDLE
        return (
            SequenceState.RETRACE_HELD
            if self.pending.retrace_index is not None
            else SequenceState.BURST_ARMED
        )

    def mark_trade(self, signal_index: int) -> None:
        self.last_trade_index = signal_index
        self.pending = None

    def clear_pending(self, reason_code: str, bar: FlowBar) -> None:
        pending = self.pending
        if pending is None:
            return
        previous = self.state.value
        self._emit(
            scenario_id=pending.scenario_id,
            event_type="SCENARIO_CANCELLED",
            event_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            previous_state=previous,
            next_state="CANCELLED",
            reason_code=reason_code,
            reference_price=bar.close,
        )
        self.pending = None

    def on_bar(self, bar: FlowBar, *, trading_available: bool = True) -> list[QHSetup]:
        setup = self._advance(bar, trading_available=trading_available)
        if setup is not None:
            return [setup]
        if self.pending is not None or not trading_available:
            return []
        if bar.index - self.last_trade_index <= self.config.cooldown_bars:
            return []
        self._detect_burst(bar)
        return []

    def _detect_burst(self, bar: FlowBar) -> None:
        if bar.minute not in (0, 15, 30, 45):
            return
        if self.config.top_of_hour_only and bar.minute != 0:
            return
        direction_value = 1 if bar.imbalance > 0 else -1
        directional_body = direction_value * (bar.close - bar.open)
        if (
            abs(bar.imbalance) < self.config.minimum_abs_imbalance
            or bar.volume_ratio < self.config.minimum_volume_ratio
            or bar.trade_ratio < self.config.minimum_trade_ratio
            or directional_body < self.config.minimum_body_atr * bar.atr
            or not isfinite(bar.lag_mean4)
            or abs(bar.lag_mean4) < self.config.minimum_lag_abs
            or bar.lag_mean4 * direction_value <= 0
        ):
            return
        if direction_value > 0 and bar.close_location < 0.62:
            return
        if direction_value < 0 and bar.close_location > 0.38:
            return
        if not self._regime_allows(bar, direction_value):
            return

        self._scenario_counter += 1
        direction = Direction.LONG if direction_value > 0 else Direction.SHORT
        scenario_id = f"qh-{self._scenario_counter:06d}"
        self.pending = ArmedBurst(
            scenario_id=scenario_id,
            direction=direction,
            burst_index=bar.index,
            burst_time_ns=bar.ts_event_ns,
            expiry_index=bar.index + self.config.maximum_retrace_bars,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            trade_count=bar.trade_count,
            imbalance=bar.imbalance,
            atr=bar.atr,
            previous_session_high=bar.previous_session_high,
            previous_session_low=bar.previous_session_low,
            previous_session_direction=bar.previous_session_direction,
            efficiency_60m=bar.efficiency_60m,
            direction_60m=bar.direction_60m,
        )
        self._emit(
            scenario_id=scenario_id,
            event_type="PERIODIC_FLOW_BURST",
            event_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            previous_state="IDLE",
            next_state=SequenceState.BURST_ARMED.value,
            reason_code=f"QH_BURST_{direction.value}_{self.config.regime_mode}",
            reference_price=bar.close,
            details={
                "imbalance": bar.imbalance,
                "volume_ratio": bar.volume_ratio,
                "trade_ratio": bar.trade_ratio,
                "body_atr": bar.body / bar.atr,
                "lag_mean4": bar.lag_mean4,
                "efficiency_60m": bar.efficiency_60m,
                "direction_60m": bar.direction_60m,
                "previous_session_direction": bar.previous_session_direction,
                "expiry_index": self.pending.expiry_index,
            },
        )

    def _regime_allows(self, bar: FlowBar, direction: int) -> bool:
        if self.config.regime_mode == "NONE":
            return True
        if self.config.regime_mode == "PREVIOUS_SESSION":
            return int(bar.previous_session_direction) == direction
        return (
            isfinite(bar.efficiency_60m)
            and bar.efficiency_60m >= self.config.minimum_efficiency_60m
            and int(bar.direction_60m) == direction
        )

    def _advance(self, bar: FlowBar, *, trading_available: bool) -> QHSetup | None:
        pending = self.pending
        if pending is None or bar.index <= pending.burst_index:
            return None
        if bar.index > pending.expiry_index:
            self.clear_pending("SEQUENCE_TIMEOUT", bar)
            return None

        direction = 1 if pending.direction is Direction.LONG else -1
        if pending.retrace_index is None:
            if self._burst_origin_invalidated(pending, bar, direction):
                self.clear_pending("BURST_ORIGIN_INVALIDATED", bar)
                return None
            if not self._is_contracted_retrace(pending, bar, direction):
                return None
            pending.retrace_index = bar.index
            pending.retrace_time_ns = bar.ts_event_ns
            pending.retrace_high = bar.high
            pending.retrace_low = bar.low
            pending.retrace_volume = bar.volume
            pending.retrace_trade_count = bar.trade_count
            pending.retrace_imbalance = bar.imbalance
            pending.expiry_index = bar.index + self.config.maximum_reacceleration_bars
            self._emit(
                scenario_id=pending.scenario_id,
                event_type="CONTRACTED_RETRACE_HELD",
                event_time_ns=bar.ts_event_ns,
                observed_time_ns=bar.ts_event_ns,
                previous_state=SequenceState.BURST_ARMED.value,
                next_state=SequenceState.RETRACE_HELD.value,
                reason_code=f"LOW_ENERGY_RETRACE_{pending.direction.value}",
                reference_price=bar.close,
                details={
                    "volume_fraction": bar.volume / max(pending.volume, 1e-12),
                    "trade_fraction": bar.trade_count / max(pending.trade_count, 1e-12),
                    "retrace_imbalance": bar.imbalance,
                    "reacceleration_expiry_index": pending.expiry_index,
                },
            )
            return None

        if self._retrace_invalidated(pending, bar, direction):
            self.clear_pending("RETRACE_INVALIDATED", bar)
            return None
        if not trading_available or not self._is_reacceleration(pending, bar, direction):
            return None
        assert pending.retrace_low is not None and pending.retrace_high is not None
        entry = bar.close
        structural_stop = (
            pending.retrace_low - self.config.stop_buffer_atr * bar.atr
            if direction > 0
            else pending.retrace_high + self.config.stop_buffer_atr * bar.atr
        )
        minimum_distance = self.config.minimum_stop_atr * bar.atr
        if direction > 0:
            stop = min(structural_stop, entry - minimum_distance)
        else:
            stop = max(structural_stop, entry + minimum_distance)
        target = self._external_target(pending, entry, direction)
        setup = QHSetup(
            scenario_id=pending.scenario_id,
            family="QH_FLOW_CONTINUATION",
            direction=pending.direction,
            signal_index=bar.index,
            signal_time_ns=bar.ts_event_ns,
            estimated_entry=entry,
            structural_stop=stop,
            external_target=target,
            atr=bar.atr,
            details={
                "burst_index": pending.burst_index,
                "retrace_index": pending.retrace_index,
                "minutes_sequence": bar.index - pending.burst_index,
                "burst_imbalance": pending.imbalance,
                "retrace_imbalance": pending.retrace_imbalance,
                "entry_imbalance": bar.imbalance,
                "efficiency_60m": pending.efficiency_60m,
                "previous_session_high": pending.previous_session_high,
                "previous_session_low": pending.previous_session_low,
                "regime_mode": self.config.regime_mode,
            },
        )
        self._emit(
            scenario_id=pending.scenario_id,
            event_type="SEQUENCE_CONFIRMED",
            event_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            previous_state=SequenceState.RETRACE_HELD.value,
            next_state="CONFIRMED",
            reason_code=f"INDEPENDENT_REACCELERATION_{pending.direction.value}",
            reference_price=entry,
            details={"stop": stop, "target": target},
        )
        return setup

    def _burst_origin_invalidated(self, pending: ArmedBurst, bar: FlowBar, direction: int) -> bool:
        if direction > 0:
            return (
                bar.close < pending.open - self.config.maximum_origin_close_violation_atr * pending.atr
                or bar.low < pending.open - self.config.maximum_origin_extreme_violation_atr * pending.atr
            )
        return (
            bar.close > pending.open + self.config.maximum_origin_close_violation_atr * pending.atr
            or bar.high > pending.open + self.config.maximum_origin_extreme_violation_atr * pending.atr
        )

    def _is_contracted_retrace(self, pending: ArmedBurst, bar: FlowBar, direction: int) -> bool:
        body = abs(pending.close - pending.open)
        if direction > 0:
            reached_value = bar.low <= pending.close - self.config.minimum_retrace_fraction * body
            origin_held = bar.close >= pending.open - self.config.maximum_origin_close_violation_atr * pending.atr
        else:
            reached_value = bar.high >= pending.close + self.config.minimum_retrace_fraction * body
            origin_held = bar.close <= pending.open + self.config.maximum_origin_close_violation_atr * pending.atr
        return (
            reached_value
            and origin_held
            and bar.volume <= self.config.maximum_retrace_volume_fraction * pending.volume
            and bar.trade_count <= self.config.maximum_retrace_trade_fraction * pending.trade_count
            and abs(bar.imbalance)
            <= self.config.maximum_retrace_imbalance_fraction * abs(pending.imbalance)
            + self.config.retrace_imbalance_allowance
        )

    def _retrace_invalidated(self, pending: ArmedBurst, bar: FlowBar, direction: int) -> bool:
        if direction > 0:
            return bar.close < pending.open - self.config.maximum_origin_close_violation_atr * pending.atr
        return bar.close > pending.open + self.config.maximum_origin_close_violation_atr * pending.atr

    def _is_reacceleration(self, pending: ArmedBurst, bar: FlowBar, direction: int) -> bool:
        assert pending.retrace_high is not None
        assert pending.retrace_low is not None
        assert pending.retrace_volume is not None
        assert pending.retrace_trade_count is not None
        directional_body = direction * (bar.close - bar.open)
        directional_flow = direction * bar.imbalance
        if direction > 0:
            displaced = bar.close >= pending.retrace_high + self.config.reacceleration_break_atr * bar.atr
            located = bar.close_location >= 0.62
        else:
            displaced = bar.close <= pending.retrace_low - self.config.reacceleration_break_atr * bar.atr
            located = bar.close_location <= 0.38
        return (
            displaced
            and located
            and directional_body >= self.config.minimum_reacceleration_body_atr * bar.atr
            and directional_flow >= self.config.minimum_reacceleration_imbalance
            and bar.volume >= self.config.reacceleration_volume_multiplier * pending.retrace_volume
            and bar.trade_count >= pending.retrace_trade_count
        )

    def _external_target(self, pending: ArmedBurst, entry: float, direction: int) -> float:
        span = pending.previous_session_high - pending.previous_session_low
        if span <= 0:
            raise ValueError("completed session range must be positive")
        if direction > 0:
            return (
                pending.previous_session_high
                if pending.previous_session_high > entry
                else pending.previous_session_high + self.config.external_range_extension * span
            )
        return (
            pending.previous_session_low
            if pending.previous_session_low < entry
            else pending.previous_session_low - self.config.external_range_extension * span
        )

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


def group_events_by_reason(events: Iterable[LogicEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.reason_code] = counts.get(event.reason_code, 0) + 1
    return counts
