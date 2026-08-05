"""Causal market-auction state machine for candidate 01.

The module deliberately contains no execution or accounting code.  It turns a
strictly time-ordered stream of completed one-minute auction bars into explicit
scenario transitions and, occasionally, a risk-defined trade plan.  Nautilus
Trader remains responsible for orders, fills, positions, fees, and account NAV.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from enum import Enum
from math import isfinite, sqrt
from statistics import fmean
from typing import Any, Deque, Iterable


NS_PER_MINUTE = 60_000_000_000


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


class Response(str, Enum):
    REJECTION = "REJECTION"
    ACCEPTANCE = "ACCEPTANCE"


class Phase(str, Enum):
    COLLECTING = "COLLECTING"
    WATCHING = "WATCHING"
    SWEPT = "SWEPT"
    ACCEPTING = "ACCEPTING"
    ARMED_REJECTION = "ARMED_REJECTION"
    ARMED_ACCEPTANCE = "ARMED_ACCEPTANCE"
    COOLDOWN = "COOLDOWN"


@dataclass(frozen=True, slots=True)
class AuctionBar:
    """One completed, causally observable one-minute auction summary."""

    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    base_volume: float
    quote_volume: float
    taker_buy_quote_volume: float

    def __post_init__(self) -> None:
        if self.ts_event_ns < 0:
            raise ValueError("ts_event_ns must be non-negative")
        values = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.base_volume,
            self.quote_volume,
            self.taker_buy_quote_volume,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("bar values must be finite")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.high < self.low:
            raise ValueError("high cannot be below low")
        if self.base_volume < 0 or self.quote_volume < 0:
            raise ValueError("volume cannot be negative")
        if self.taker_buy_quote_volume < 0:
            raise ValueError("taker buy volume cannot be negative")
        if self.taker_buy_quote_volume > self.quote_volume * 1.000001:
            raise ValueError("taker buy quote volume cannot exceed quote volume")

    @property
    def signed_aggressive_quote(self) -> float:
        """Buyer-initiated minus seller-initiated quote notional."""

        return 2.0 * self.taker_buy_quote_volume - self.quote_volume

    @property
    def aggressive_imbalance(self) -> float:
        if self.quote_volume <= 0.0:
            return 0.0
        return self.signed_aggressive_quote / self.quote_volume


@dataclass(frozen=True, slots=True)
class CandidateConfig:
    """Small structural parameter set, intentionally not instrument-specific."""

    range_minutes: int = 240
    min_anchor_fraction: float = 0.90
    atr_lookback: int = 60
    flow_lookback: int = 60
    volume_lookback: int = 60
    structure_lookback: int = 8
    min_history: int = 60
    min_sweep_atr: float = 0.06
    max_sweep_atr: float = 1.10
    close_inside_atr: float = 0.03
    min_activity_z: float = -0.25
    min_displacement_atr: float = 0.42
    min_displacement_flow_z: float = 0.55
    confirmation_bars: int = 8
    entry_wait_bars: int = 12
    stop_buffer_atr: float = 0.12
    min_reward_risk: float = 1.65
    acceptance_close_atr: float = 0.16
    acceptance_flow_z: float = 0.70
    acceptance_confirm_bars: int = 3
    acceptance_retest_atr: float = 0.14
    acceptance_projection: float = 0.85
    max_trades_per_block: int = 1
    max_hold_bars: int = 120
    cooldown_bars: int = 15
    enable_rejection: bool = True
    enable_acceptance: bool = True

    def __post_init__(self) -> None:
        integer_fields = {
            "range_minutes": self.range_minutes,
            "atr_lookback": self.atr_lookback,
            "flow_lookback": self.flow_lookback,
            "volume_lookback": self.volume_lookback,
            "structure_lookback": self.structure_lookback,
            "min_history": self.min_history,
            "confirmation_bars": self.confirmation_bars,
            "entry_wait_bars": self.entry_wait_bars,
            "acceptance_confirm_bars": self.acceptance_confirm_bars,
            "max_trades_per_block": self.max_trades_per_block,
            "max_hold_bars": self.max_hold_bars,
            "cooldown_bars": self.cooldown_bars,
        }
        if any(value <= 0 for value in integer_fields.values()):
            raise ValueError(f"positive integer parameters required: {integer_fields}")
        if self.range_minutes < 60:
            raise ValueError("range_minutes must be at least 60")
        if not 0.5 <= self.min_anchor_fraction <= 1.0:
            raise ValueError("min_anchor_fraction must be in [0.5, 1.0]")
        if not 0.0 < self.min_sweep_atr < self.max_sweep_atr:
            raise ValueError("invalid sweep bounds")
        if self.min_reward_risk <= 1.0:
            raise ValueError("min_reward_risk must exceed one")
        if self.acceptance_projection <= 0.0:
            raise ValueError("acceptance_projection must be positive")

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "CandidateConfig":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown candidate config keys: {unknown}")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DealingRange:
    block_id: int
    start_ns: int
    end_ns: int
    open: float
    high: float
    low: float
    close: float
    bars: int

    @property
    def midpoint(self) -> float:
        return 0.5 * (self.high + self.low)

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class ScenarioTransition:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.reference_price is not None:
            payload["reference_price"] = f"{self.reference_price:.12g}"
        return payload


@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    side: Side
    response: Response
    signal_time_ns: int
    observed_time_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    anchor_high: float
    anchor_low: float
    sweep_extreme: float
    atr: float
    estimated_reward_risk: float
    max_hold_bars: int
    reason_code: str

    def __post_init__(self) -> None:
        if self.side is Side.LONG:
            if not self.stop_price < self.expected_entry < self.target_price:
                raise ValueError("long trade prices must satisfy stop < entry < target")
        else:
            if not self.target_price < self.expected_entry < self.stop_price:
                raise ValueError("short trade prices must satisfy target < entry < stop")
        if self.estimated_reward_risk <= 1.0:
            raise ValueError("trade plan must have positive asymmetric payoff")


@dataclass(slots=True)
class _BlockBuilder:
    block_id: int
    start_ns: int
    open: float
    high: float
    low: float
    close: float
    bars: int = 1

    def update(self, bar: AuctionBar) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.bars += 1

    def finish(self, range_ns: int) -> DealingRange:
        return DealingRange(
            block_id=self.block_id,
            start_ns=self.start_ns,
            end_ns=self.start_ns + range_ns,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            bars=self.bars,
        )


@dataclass(slots=True)
class _Scenario:
    scenario_id: str
    phase: Phase
    side: Side
    response: Response
    anchor: DealingRange
    started_index: int
    sweep_extreme: float
    internal_break: float
    anchor_level: float
    flow_sum: float = 0.0
    confirm_count: int = 0
    zone_low: float | None = None
    zone_high: float | None = None
    expiry_index: int = 0


class AuctionStateMachine:
    """Stateful causal detector for external-liquidity auction responses."""

    def __init__(self, config: CandidateConfig, instrument_id: str = "BTCUSDT.BINANCE") -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.phase = Phase.COLLECTING
        self.anchor: DealingRange | None = None
        self._range_ns = config.range_minutes * NS_PER_MINUTE
        self._builder: _BlockBuilder | None = None
        self._history: Deque[AuctionBar] = deque(maxlen=max(256, config.min_history + 16))
        self._true_ranges: Deque[float] = deque(maxlen=config.atr_lookback)
        self._flow_values: Deque[float] = deque(maxlen=config.flow_lookback)
        self._volume_values: Deque[float] = deque(maxlen=config.volume_lookback)
        self._last_close: float | None = None
        self._bar_index = -1
        self._scenario: _Scenario | None = None
        self._transitions: list[ScenarioTransition] = []
        self._last_ts_ns = -1
        self._trades_in_block = 0
        self._cooldown_until = -1

    @property
    def transitions(self) -> tuple[ScenarioTransition, ...]:
        return tuple(self._transitions)

    @property
    def current_scenario_id(self) -> str | None:
        return self._scenario.scenario_id if self._scenario else None

    def _zscore(self, value: float, history: Iterable[float]) -> float:
        values = tuple(history)
        if len(values) < 20:
            return 0.0
        mean = fmean(values)
        variance = fmean((item - mean) ** 2 for item in values)
        if variance <= 1e-18:
            return 0.0
        return (value - mean) / sqrt(variance)

    def _prior_atr(self) -> float | None:
        if len(self._true_ranges) < max(20, self.config.atr_lookback // 2):
            return None
        return fmean(self._true_ranges)

    def _emit(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        previous: Phase,
        next_: Phase,
        reason: str,
        price: float | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._transitions.append(
            ScenarioTransition(
                scenario_id=scenario_id,
                event_type=event_type,
                event_time_ns=event_time_ns,
                observed_time_ns=self._last_ts_ns,
                previous_state=previous.value,
                next_state=next_.value,
                reason_code=reason,
                reference_price=price,
                details=dict(details or {}),
            ),
        )
        self.phase = next_

    def _scenario_id(self, bar: AuctionBar, side: Side, response: Response) -> str:
        return (
            f"{self.instrument_id}:{bar.ts_event_ns}:"
            f"{response.value.lower()}:{side.value.lower()}"
        )

    def _roll_block(self, bar: AuctionBar) -> None:
        block_id = bar.ts_event_ns // self._range_ns
        block_start = block_id * self._range_ns
        if self._builder is None:
            self._builder = _BlockBuilder(
                block_id=block_id,
                start_ns=block_start,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
            )
            return
        if block_id == self._builder.block_id:
            self._builder.update(bar)
            return

        completed = self._builder.finish(self._range_ns)
        expected = self.config.range_minutes
        if completed.bars >= int(expected * self.config.min_anchor_fraction):
            self.anchor = completed
            previous = self.phase
            sid = f"{self.instrument_id}:range:{completed.block_id}"
            self._emit(
                scenario_id=sid,
                event_type="DEALING_RANGE_CONFIRMED",
                event_time_ns=completed.end_ns,
                previous=previous,
                next_=Phase.WATCHING,
                reason="COMPLETED_RANGE_AVAILABLE",
                price=completed.midpoint,
                details={
                    "block_id": completed.block_id,
                    "high": completed.high,
                    "low": completed.low,
                    "bars": completed.bars,
                },
            )
        else:
            self.anchor = None
            self.phase = Phase.COLLECTING

        self._scenario = None
        self._trades_in_block = 0
        self._cooldown_until = -1
        self._builder = _BlockBuilder(
            block_id=block_id,
            start_ns=block_start,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
        )

    def _expire(self, bar: AuctionBar, reason: str) -> None:
        scenario = self._scenario
        if scenario is None:
            return
        previous = scenario.phase
        self._emit(
            scenario_id=scenario.scenario_id,
            event_type="SCENARIO_INVALIDATED",
            event_time_ns=bar.ts_event_ns,
            previous=previous,
            next_=Phase.WATCHING,
            reason=reason,
            price=bar.close,
        )
        self._scenario = None

    def _start_rejection(
        self,
        bar: AuctionBar,
        *,
        side: Side,
        sweep_extreme: float,
        anchor_level: float,
        internal_break: float,
        atr: float,
        flow_z: float,
        volume_z: float,
    ) -> None:
        assert self.anchor is not None
        scenario_id = self._scenario_id(bar, side, Response.REJECTION)
        previous = self.phase
        self._scenario = _Scenario(
            scenario_id=scenario_id,
            phase=Phase.SWEPT,
            side=side,
            response=Response.REJECTION,
            anchor=self.anchor,
            started_index=self._bar_index,
            sweep_extreme=sweep_extreme,
            internal_break=internal_break,
            anchor_level=anchor_level,
            expiry_index=self._bar_index + self.config.confirmation_bars,
        )
        self._emit(
            scenario_id=scenario_id,
            event_type="EXTERNAL_LIQUIDITY_SWEPT",
            event_time_ns=bar.ts_event_ns,
            previous=previous,
            next_=Phase.SWEPT,
            reason="CLOSE_RETURNED_INSIDE_COMPLETED_RANGE",
            price=sweep_extreme,
            details={
                "side": side.value,
                "anchor_level": anchor_level,
                "internal_break": internal_break,
                "atr": atr,
                "flow_z": flow_z,
                "volume_z": volume_z,
            },
        )
        self._scenario.phase = Phase.SWEPT

    def _start_acceptance(
        self,
        bar: AuctionBar,
        *,
        side: Side,
        sweep_extreme: float,
        anchor_level: float,
        atr: float,
        flow_z: float,
        volume_z: float,
    ) -> None:
        assert self.anchor is not None
        scenario_id = self._scenario_id(bar, side, Response.ACCEPTANCE)
        previous = self.phase
        self._scenario = _Scenario(
            scenario_id=scenario_id,
            phase=Phase.ACCEPTING,
            side=side,
            response=Response.ACCEPTANCE,
            anchor=self.anchor,
            started_index=self._bar_index,
            sweep_extreme=sweep_extreme,
            internal_break=anchor_level,
            anchor_level=anchor_level,
            flow_sum=flow_z,
            confirm_count=1,
            expiry_index=self._bar_index + self.config.acceptance_confirm_bars,
        )
        self._emit(
            scenario_id=scenario_id,
            event_type="RANGE_BOUNDARY_ACCEPTED",
            event_time_ns=bar.ts_event_ns,
            previous=previous,
            next_=Phase.ACCEPTING,
            reason="CLOSE_AND_AGGRESSIVE_FLOW_HELD_OUTSIDE_RANGE",
            price=bar.close,
            details={
                "side": side.value,
                "anchor_level": anchor_level,
                "atr": atr,
                "flow_z": flow_z,
                "volume_z": volume_z,
            },
        )
        self._scenario.phase = Phase.ACCEPTING

    def _watch_for_auction(
        self,
        bar: AuctionBar,
        *,
        atr: float,
        flow_z: float,
        volume_z: float,
    ) -> None:
        anchor = self.anchor
        if anchor is None or anchor.width <= 0.0:
            return
        if self._bar_index < self._cooldown_until:
            return
        if self._trades_in_block >= self.config.max_trades_per_block:
            return
        if len(self._history) < self.config.structure_lookback:
            return

        high_penetration = bar.high - anchor.high
        low_penetration = anchor.low - bar.low
        high_cross = high_penetration >= self.config.min_sweep_atr * atr
        low_cross = low_penetration >= self.config.min_sweep_atr * atr
        if high_cross and low_cross:
            sid = self._scenario_id(bar, Side.SHORT, Response.REJECTION)
            self._emit(
                scenario_id=sid,
                event_type="AMBIGUOUS_RANGE_EXPANSION",
                event_time_ns=bar.ts_event_ns,
                previous=self.phase,
                next_=Phase.WATCHING,
                reason="BOTH_RANGE_SIDES_CROSSED_IN_ONE_BAR",
                price=bar.close,
                details={"anchor_high": anchor.high, "anchor_low": anchor.low, "atr": atr},
            )
            return

        activity_ok = volume_z >= self.config.min_activity_z
        prior = tuple(self._history)[-self.config.structure_lookback :]

        if high_cross:
            inside = bar.close <= anchor.high - self.config.close_inside_atr * atr
            shallow_enough = high_penetration <= self.config.max_sweep_atr * atr
            if self.config.enable_rejection and inside and shallow_enough and activity_ok:
                self._start_rejection(
                    bar,
                    side=Side.SHORT,
                    sweep_extreme=bar.high,
                    anchor_level=anchor.high,
                    internal_break=min(item.low for item in prior),
                    atr=atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )
                return
            accepted = (
                self.config.enable_acceptance
                and bar.close >= anchor.high + self.config.acceptance_close_atr * atr
                and flow_z >= self.config.acceptance_flow_z
                and activity_ok
            )
            if accepted:
                self._start_acceptance(
                    bar,
                    side=Side.LONG,
                    sweep_extreme=bar.high,
                    anchor_level=anchor.high,
                    atr=atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )
                return

        if low_cross:
            inside = bar.close >= anchor.low + self.config.close_inside_atr * atr
            shallow_enough = low_penetration <= self.config.max_sweep_atr * atr
            if self.config.enable_rejection and inside and shallow_enough and activity_ok:
                self._start_rejection(
                    bar,
                    side=Side.LONG,
                    sweep_extreme=bar.low,
                    anchor_level=anchor.low,
                    internal_break=max(item.high for item in prior),
                    atr=atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )
                return
            accepted = (
                self.config.enable_acceptance
                and bar.close <= anchor.low - self.config.acceptance_close_atr * atr
                and flow_z <= -self.config.acceptance_flow_z
                and activity_ok
            )
            if accepted:
                self._start_acceptance(
                    bar,
                    side=Side.SHORT,
                    sweep_extreme=bar.low,
                    anchor_level=anchor.low,
                    atr=atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )

    def _advance_rejection(
        self,
        bar: AuctionBar,
        scenario: _Scenario,
        *,
        atr: float,
        flow_z: float,
    ) -> TradePlan | None:
        side = scenario.side
        invalidation = self.config.stop_buffer_atr * atr
        if side is Side.SHORT and bar.high > scenario.sweep_extreme + invalidation:
            self._expire(bar, "SWEEP_EXTREME_RECLAIMED")
            return None
        if side is Side.LONG and bar.low < scenario.sweep_extreme - invalidation:
            self._expire(bar, "SWEEP_EXTREME_RECLAIMED")
            return None

        if scenario.phase is Phase.SWEPT:
            if self._bar_index > scenario.expiry_index:
                self._expire(bar, "NO_OPPOSING_DISPLACEMENT_IN_TIME")
                return None
            body = abs(bar.close - bar.open)
            if side is Side.SHORT:
                displaced = (
                    bar.close < scenario.internal_break
                    and bar.close < bar.open
                    and body >= self.config.min_displacement_atr * atr
                    and flow_z <= -self.config.min_displacement_flow_z
                )
            else:
                displaced = (
                    bar.close > scenario.internal_break
                    and bar.close > bar.open
                    and body >= self.config.min_displacement_atr * atr
                    and flow_z >= self.config.min_displacement_flow_z
                )
            if not displaced:
                return None

            two_back = tuple(self._history)[-2] if len(self._history) >= 2 else None
            if side is Side.SHORT and two_back is not None and bar.high < two_back.low:
                zone_low, zone_high = bar.high, two_back.low
                zone_reason = "CAUSAL_BEARISH_FVG"
            elif side is Side.LONG and two_back is not None and bar.low > two_back.high:
                zone_low, zone_high = two_back.high, bar.low
                zone_reason = "CAUSAL_BULLISH_FVG"
            elif side is Side.SHORT:
                impulse = max(bar.open - bar.close, 0.01 * atr)
                zone_low = bar.close + 0.35 * impulse
                zone_high = bar.close + 0.72 * impulse
                zone_reason = "DISPLACEMENT_BODY_RETRACE_ZONE"
            else:
                impulse = max(bar.close - bar.open, 0.01 * atr)
                zone_low = bar.close - 0.72 * impulse
                zone_high = bar.close - 0.35 * impulse
                zone_reason = "DISPLACEMENT_BODY_RETRACE_ZONE"

            scenario.zone_low = min(zone_low, zone_high)
            scenario.zone_high = max(zone_low, zone_high)
            scenario.phase = Phase.ARMED_REJECTION
            scenario.expiry_index = self._bar_index + self.config.entry_wait_bars
            self._emit(
                scenario_id=scenario.scenario_id,
                event_type="OPPOSING_DISPLACEMENT_CONFIRMED",
                event_time_ns=bar.ts_event_ns,
                previous=Phase.SWEPT,
                next_=Phase.ARMED_REJECTION,
                reason=zone_reason,
                price=bar.close,
                details={
                    "flow_z": flow_z,
                    "atr": atr,
                    "zone_low": scenario.zone_low,
                    "zone_high": scenario.zone_high,
                    "internal_break": scenario.internal_break,
                },
            )
            return None

        if scenario.phase is not Phase.ARMED_REJECTION:
            return None
        if self._bar_index > scenario.expiry_index:
            self._expire(bar, "NO_CAUSAL_RETRACE_IN_TIME")
            return None
        assert scenario.zone_low is not None and scenario.zone_high is not None
        zone_mid = 0.5 * (scenario.zone_low + scenario.zone_high)
        touched = bar.high >= scenario.zone_low and bar.low <= scenario.zone_high
        if side is Side.SHORT:
            confirmed = (
                touched
                and scenario.zone_low <= bar.close <= zone_mid
                and bar.close < bar.open
                and flow_z <= 0.35
            )
            stop = scenario.sweep_extreme + self.config.stop_buffer_atr * atr
            target = scenario.anchor.low
        else:
            confirmed = (
                touched
                and zone_mid <= bar.close <= scenario.zone_high
                and bar.close > bar.open
                and flow_z >= -0.35
            )
            stop = scenario.sweep_extreme - self.config.stop_buffer_atr * atr
            target = scenario.anchor.high
        if not confirmed:
            return None
        return self._build_plan(
            bar=bar,
            scenario=scenario,
            atr=atr,
            stop=stop,
            target=target,
            reason="RETRACE_REJECTED_TOWARD_OPPOSING_LIQUIDITY",
        )

    def _advance_acceptance(
        self,
        bar: AuctionBar,
        scenario: _Scenario,
        *,
        atr: float,
        flow_z: float,
    ) -> TradePlan | None:
        side = scenario.side
        anchor_level = scenario.anchor_level
        if scenario.phase is Phase.ACCEPTING:
            if self._bar_index > scenario.expiry_index:
                self._expire(bar, "OUTSIDE_CLOSE_NOT_CONFIRMED")
                return None
            if side is Side.LONG:
                still_outside = bar.close > anchor_level
                directional_flow = flow_z > 0.0
            else:
                still_outside = bar.close < anchor_level
                directional_flow = flow_z < 0.0
            if not still_outside:
                self._expire(bar, "RANGE_REENTERED_BEFORE_ACCEPTANCE")
                return None
            scenario.flow_sum += flow_z
            scenario.confirm_count += 1
            if scenario.confirm_count < 2 or not directional_flow:
                return None

            scenario.phase = Phase.ARMED_ACCEPTANCE
            scenario.expiry_index = self._bar_index + self.config.entry_wait_bars
            scenario.zone_low = anchor_level - self.config.acceptance_retest_atr * atr
            scenario.zone_high = anchor_level + self.config.acceptance_retest_atr * atr
            self._emit(
                scenario_id=scenario.scenario_id,
                event_type="OUTSIDE_VALUE_ACCEPTED",
                event_time_ns=bar.ts_event_ns,
                previous=Phase.ACCEPTING,
                next_=Phase.ARMED_ACCEPTANCE,
                reason="SECOND_DIRECTIONAL_CLOSE_WITH_FLOW",
                price=bar.close,
                details={
                    "confirm_count": scenario.confirm_count,
                    "flow_sum": scenario.flow_sum,
                    "zone_low": scenario.zone_low,
                    "zone_high": scenario.zone_high,
                },
            )
            return None

        if scenario.phase is not Phase.ARMED_ACCEPTANCE:
            return None
        if self._bar_index > scenario.expiry_index:
            self._expire(bar, "NO_BOUNDARY_RETEST_IN_TIME")
            return None
        assert scenario.zone_low is not None and scenario.zone_high is not None
        touched = bar.high >= scenario.zone_low and bar.low <= scenario.zone_high
        if side is Side.LONG:
            confirmed = touched and bar.close > anchor_level and bar.close > bar.open and flow_z >= -0.20
            stop = min(bar.low, anchor_level - self.config.stop_buffer_atr * atr)
            target = anchor_level + scenario.anchor.width * self.config.acceptance_projection
        else:
            confirmed = touched and bar.close < anchor_level and bar.close < bar.open and flow_z <= 0.20
            stop = max(bar.high, anchor_level + self.config.stop_buffer_atr * atr)
            target = anchor_level - scenario.anchor.width * self.config.acceptance_projection
        if not confirmed:
            return None
        return self._build_plan(
            bar=bar,
            scenario=scenario,
            atr=atr,
            stop=stop,
            target=target,
            reason="ACCEPTED_BOUNDARY_HELD_ON_RETEST",
        )

    def _build_plan(
        self,
        *,
        bar: AuctionBar,
        scenario: _Scenario,
        atr: float,
        stop: float,
        target: float,
        reason: str,
    ) -> TradePlan | None:
        entry = bar.close
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0.0 or reward <= 0.0:
            self._expire(bar, "NON_POSITIVE_RISK_OR_REWARD")
            return None
        reward_risk = reward / risk
        if reward_risk < self.config.min_reward_risk:
            self._expire(bar, "OPPOSING_LIQUIDITY_TOO_CLOSE_FOR_COST_ROBUST_PAYOFF")
            return None

        plan = TradePlan(
            scenario_id=scenario.scenario_id,
            side=scenario.side,
            response=scenario.response,
            signal_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            anchor_high=scenario.anchor.high,
            anchor_low=scenario.anchor.low,
            sweep_extreme=scenario.sweep_extreme,
            atr=atr,
            estimated_reward_risk=reward_risk,
            max_hold_bars=self.config.max_hold_bars,
            reason_code=reason,
        )
        previous = scenario.phase
        self._emit(
            scenario_id=scenario.scenario_id,
            event_type="TRADE_PLAN_EMITTED",
            event_time_ns=bar.ts_event_ns,
            previous=previous,
            next_=Phase.COOLDOWN,
            reason=reason,
            price=entry,
            details={
                "side": scenario.side.value,
                "response": scenario.response.value,
                "stop": stop,
                "target": target,
                "estimated_reward_risk": reward_risk,
                "atr": atr,
            },
        )
        self._scenario = None
        self._trades_in_block += 1
        self._cooldown_until = self._bar_index + self.config.cooldown_bars
        return plan

    def on_bar(self, bar: AuctionBar) -> TradePlan | None:
        """Process one completed bar and return at most one causal trade plan."""

        if bar.ts_event_ns <= self._last_ts_ns:
            raise ValueError("bars must arrive in strictly increasing event-time order")
        self._bar_index += 1
        self._last_ts_ns = bar.ts_event_ns

        prior_atr = self._prior_atr()
        flow_z = self._zscore(bar.aggressive_imbalance, self._flow_values)
        volume_z = self._zscore(bar.quote_volume, self._volume_values)

        self._roll_block(bar)
        plan: TradePlan | None = None
        enough_history = len(self._history) >= self.config.min_history
        if prior_atr is not None and prior_atr > 0.0 and enough_history and self.anchor is not None:
            scenario = self._scenario
            if scenario is None:
                self._watch_for_auction(
                    bar,
                    atr=prior_atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )
            else:
                if scenario.response is Response.REJECTION:
                    plan = self._advance_rejection(
                        bar,
                        scenario,
                        atr=prior_atr,
                        flow_z=flow_z,
                    )
                else:
                    plan = self._advance_acceptance(
                        bar,
                        scenario,
                        atr=prior_atr,
                        flow_z=flow_z,
                    )

        if self._last_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - self._last_close),
                abs(bar.low - self._last_close),
            )
        self._true_ranges.append(true_range)
        self._flow_values.append(bar.aggressive_imbalance)
        self._volume_values.append(bar.quote_volume)
        self._history.append(bar)
        self._last_close = bar.close
        return plan
