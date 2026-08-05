"""Causal failed-auction state machine for candidate 01.

The detector separates market events from the trading scenario.  It observes a
completed dealing range, then asks whether aggressive demand outside one edge
creates durable value or is rejected.  Only an explicit failure -- re-entry
plus opposite displacement and order-flow reversal -- can emit a trade plan.
NautilusTrader remains responsible for execution, fees, positions and NAV.
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
    SWEEP_FAILURE = "SWEEP_FAILURE"
    ACCEPTANCE_FAILURE = "ACCEPTANCE_FAILURE"


class Phase(str, Enum):
    COLLECTING = "COLLECTING"
    WATCHING = "WATCHING"
    OUTSIDE_TEST = "OUTSIDE_TEST"
    REENTERED = "REENTERED"
    ARMED_REVERSAL = "ARMED_REVERSAL"
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
        if self.high < self.low:
            raise ValueError("high cannot be below low")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.base_volume < 0.0 or self.quote_volume < 0.0:
            raise ValueError("volume cannot be negative")
        if self.taker_buy_quote_volume < 0.0:
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
    """Structural parameters shared across instruments and expressed in ATR units."""

    range_minutes: int = 240
    min_anchor_fraction: float = 0.90
    atr_lookback: int = 60
    flow_lookback: int = 60
    volume_lookback: int = 60
    structure_lookback: int = 6
    min_history: int = 60
    min_excursion_atr: float = 0.08
    max_excursion_atr: float = 1.80
    outside_close_atr: float = 0.08
    reentry_depth_atr: float = 0.06
    min_activity_z: float = -0.25
    attempt_flow_z: float = 0.55
    attempt_volume_z: float = 0.25
    minimum_outside_closes: int = 2
    failure_window_bars: int = 10
    confirmation_bars: int = 6
    min_displacement_atr: float = 0.38
    min_reversal_flow_z: float = 0.35
    max_structure_overshoot_atr: float = 1.0
    stop_buffer_atr: float = 0.15
    minimum_stop_atr: float = 0.65
    min_reward_risk: float = 1.35
    max_trades_per_block: int = 1
    max_hold_bars: int = 120
    cooldown_bars: int = 15
    enable_sweep_failure: bool = True
    enable_acceptance_failure: bool = True

    def __post_init__(self) -> None:
        integer_fields = {
            "range_minutes": self.range_minutes,
            "atr_lookback": self.atr_lookback,
            "flow_lookback": self.flow_lookback,
            "volume_lookback": self.volume_lookback,
            "structure_lookback": self.structure_lookback,
            "min_history": self.min_history,
            "minimum_outside_closes": self.minimum_outside_closes,
            "failure_window_bars": self.failure_window_bars,
            "confirmation_bars": self.confirmation_bars,
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
        if not 0.0 < self.min_excursion_atr < self.max_excursion_atr:
            raise ValueError("invalid excursion bounds")
        if self.outside_close_atr < 0.0 or self.reentry_depth_atr < 0.0:
            raise ValueError("boundary distances cannot be negative")
        if self.min_displacement_atr <= 0.0:
            raise ValueError("min_displacement_atr must be positive")
        if self.max_structure_overshoot_atr <= 0.0:
            raise ValueError("max_structure_overshoot_atr must be positive")
        if self.minimum_stop_atr <= self.stop_buffer_atr:
            raise ValueError("minimum_stop_atr must exceed stop_buffer_atr")
        if self.min_reward_risk <= 1.0:
            raise ValueError("min_reward_risk must exceed one")

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
    boundary: float
    excursion_extreme: float
    internal_break: float
    directional_flow_sum: float
    outside_closes: int
    max_volume_z: float
    expiry_index: int


class AuctionStateMachine:
    """Detect failed attempts to establish value outside a completed range."""

    def __init__(self, config: CandidateConfig, instrument_id: str = "BTCUSDT.BINANCE") -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.phase = Phase.COLLECTING
        self.anchor: DealingRange | None = None
        self._range_ns = config.range_minutes * NS_PER_MINUTE
        self._builder: _BlockBuilder | None = None
        history_size = max(
            256,
            config.min_history + config.failure_window_bars + config.confirmation_bars + 32,
        )
        self._history: Deque[AuctionBar] = deque(maxlen=history_size)
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
        self._emit(
            scenario_id=scenario.scenario_id,
            event_type="SCENARIO_INVALIDATED",
            event_time_ns=bar.ts_event_ns,
            previous=scenario.phase,
            next_=Phase.WATCHING,
            reason=reason,
            price=bar.close,
        )
        self._scenario = None

    @staticmethod
    def _directional_flow(side: Side, flow_z: float) -> float:
        """Flow in the failed breakout direction, expressed as a positive number."""

        # The trade side is opposite the attempted breakout direction.
        return -flow_z if side is Side.LONG else flow_z

    def _start_outside_test(
        self,
        bar: AuctionBar,
        *,
        side: Side,
        boundary: float,
        excursion_extreme: float,
        internal_break: float,
        atr: float,
        flow_z: float,
        volume_z: float,
    ) -> None:
        assert self.anchor is not None
        scenario_id = self._scenario_id(bar, side, Response.ACCEPTANCE_FAILURE)
        directional_flow = max(self._directional_flow(side, flow_z), 0.0)
        self._scenario = _Scenario(
            scenario_id=scenario_id,
            phase=Phase.OUTSIDE_TEST,
            side=side,
            response=Response.ACCEPTANCE_FAILURE,
            anchor=self.anchor,
            started_index=self._bar_index,
            boundary=boundary,
            excursion_extreme=excursion_extreme,
            internal_break=internal_break,
            directional_flow_sum=directional_flow,
            outside_closes=1,
            max_volume_z=volume_z,
            expiry_index=self._bar_index + self.config.failure_window_bars,
        )
        self._emit(
            scenario_id=scenario_id,
            event_type="OUTSIDE_AUCTION_TEST_STARTED",
            event_time_ns=bar.ts_event_ns,
            previous=self.phase,
            next_=Phase.OUTSIDE_TEST,
            reason="DIRECTIONAL_CLOSE_AND_FLOW_OUTSIDE_COMPLETED_RANGE",
            price=bar.close,
            details={
                "trade_side": side.value,
                "boundary": boundary,
                "excursion_extreme": excursion_extreme,
                "atr": atr,
                "flow_z": flow_z,
                "volume_z": volume_z,
            },
        )
        self._scenario.phase = Phase.OUTSIDE_TEST

    def _start_sweep_failure(
        self,
        bar: AuctionBar,
        *,
        side: Side,
        boundary: float,
        excursion_extreme: float,
        internal_break: float,
        atr: float,
        flow_z: float,
        volume_z: float,
    ) -> None:
        assert self.anchor is not None
        scenario_id = self._scenario_id(bar, side, Response.SWEEP_FAILURE)
        directional_flow = max(self._directional_flow(side, flow_z), 0.0)
        self._scenario = _Scenario(
            scenario_id=scenario_id,
            phase=Phase.REENTERED,
            side=side,
            response=Response.SWEEP_FAILURE,
            anchor=self.anchor,
            started_index=self._bar_index,
            boundary=boundary,
            excursion_extreme=excursion_extreme,
            internal_break=internal_break,
            directional_flow_sum=directional_flow,
            outside_closes=0,
            max_volume_z=volume_z,
            expiry_index=self._bar_index + self.config.confirmation_bars,
        )
        self._emit(
            scenario_id=scenario_id,
            event_type="LIQUIDITY_PROBE_REJECTED",
            event_time_ns=bar.ts_event_ns,
            previous=self.phase,
            next_=Phase.REENTERED,
            reason="EXCURSION_CLOSED_BACK_INSIDE_COMPLETED_RANGE",
            price=bar.close,
            details={
                "trade_side": side.value,
                "boundary": boundary,
                "excursion_extreme": excursion_extreme,
                "internal_break": internal_break,
                "atr": atr,
                "flow_z": flow_z,
                "volume_z": volume_z,
            },
        )
        self._scenario.phase = Phase.REENTERED

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
        high_cross = high_penetration >= self.config.min_excursion_atr * atr
        low_cross = low_penetration >= self.config.min_excursion_atr * atr
        if high_cross and low_cross:
            sid = self._scenario_id(bar, Side.SHORT, Response.SWEEP_FAILURE)
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

        if high_cross and high_penetration <= self.config.max_excursion_atr * atr and activity_ok:
            internal_break = min(item.low for item in prior)
            effort_ok = flow_z >= self.config.attempt_flow_z or volume_z >= self.config.attempt_volume_z
            closed_inside = bar.close <= anchor.high - self.config.reentry_depth_atr * atr
            closed_outside = bar.close >= anchor.high + self.config.outside_close_atr * atr
            if self.config.enable_sweep_failure and effort_ok and closed_inside:
                self._start_sweep_failure(
                    bar,
                    side=Side.SHORT,
                    boundary=anchor.high,
                    excursion_extreme=bar.high,
                    internal_break=internal_break,
                    atr=atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )
                return
            if self.config.enable_acceptance_failure and flow_z >= self.config.attempt_flow_z and closed_outside:
                self._start_outside_test(
                    bar,
                    side=Side.SHORT,
                    boundary=anchor.high,
                    excursion_extreme=bar.high,
                    internal_break=internal_break,
                    atr=atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )
                return

        if low_cross and low_penetration <= self.config.max_excursion_atr * atr and activity_ok:
            internal_break = max(item.high for item in prior)
            effort_ok = flow_z <= -self.config.attempt_flow_z or volume_z >= self.config.attempt_volume_z
            closed_inside = bar.close >= anchor.low + self.config.reentry_depth_atr * atr
            closed_outside = bar.close <= anchor.low - self.config.outside_close_atr * atr
            if self.config.enable_sweep_failure and effort_ok and closed_inside:
                self._start_sweep_failure(
                    bar,
                    side=Side.LONG,
                    boundary=anchor.low,
                    excursion_extreme=bar.low,
                    internal_break=internal_break,
                    atr=atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )
                return
            if self.config.enable_acceptance_failure and flow_z <= -self.config.attempt_flow_z and closed_outside:
                self._start_outside_test(
                    bar,
                    side=Side.LONG,
                    boundary=anchor.low,
                    excursion_extreme=bar.low,
                    internal_break=internal_break,
                    atr=atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )

    def _advance_outside_test(
        self,
        bar: AuctionBar,
        scenario: _Scenario,
        *,
        atr: float,
        flow_z: float,
        volume_z: float,
    ) -> TradePlan | None:
        if self._bar_index > scenario.expiry_index:
            self._expire(bar, "OUTSIDE_VALUE_PERSISTED_WITHOUT_FAILURE")
            return None

        if scenario.side is Side.SHORT:
            scenario.excursion_extreme = max(scenario.excursion_extreme, bar.high)
            still_outside = bar.close > scenario.boundary
            reentered = bar.close <= scenario.boundary - self.config.reentry_depth_atr * atr
        else:
            scenario.excursion_extreme = min(scenario.excursion_extreme, bar.low)
            still_outside = bar.close < scenario.boundary
            reentered = bar.close >= scenario.boundary + self.config.reentry_depth_atr * atr

        scenario.max_volume_z = max(scenario.max_volume_z, volume_z)
        if still_outside:
            scenario.outside_closes += 1
            scenario.directional_flow_sum += max(self._directional_flow(scenario.side, flow_z), 0.0)
            return None
        if not reentered:
            return None
        if scenario.outside_closes < self.config.minimum_outside_closes:
            self._expire(bar, "OUTSIDE_TEST_TOO_BRIEF_FOR_TRAPPED_ACCEPTANCE")
            return None

        previous = scenario.phase
        scenario.phase = Phase.REENTERED
        scenario.expiry_index = self._bar_index + self.config.confirmation_bars
        self._emit(
            scenario_id=scenario.scenario_id,
            event_type="OUTSIDE_AUCTION_FAILED",
            event_time_ns=bar.ts_event_ns,
            previous=previous,
            next_=Phase.REENTERED,
            reason="PRICE_REENTERED_RANGE_AFTER_DIRECTIONAL_OUTSIDE_FLOW",
            price=bar.close,
            details={
                "outside_closes": scenario.outside_closes,
                "directional_flow_sum": scenario.directional_flow_sum,
                "max_volume_z": scenario.max_volume_z,
                "boundary": scenario.boundary,
                "excursion_extreme": scenario.excursion_extreme,
                "internal_break": scenario.internal_break,
            },
        )
        return self._advance_reentered(bar, scenario, atr=atr, flow_z=flow_z)

    def _advance_reentered(
        self,
        bar: AuctionBar,
        scenario: _Scenario,
        *,
        atr: float,
        flow_z: float,
    ) -> TradePlan | None:
        invalidation = self.config.stop_buffer_atr * atr
        if scenario.side is Side.SHORT and bar.high > scenario.excursion_extreme + invalidation:
            self._expire(bar, "FAILED_AUCTION_EXTREME_RECLAIMED")
            return None
        if scenario.side is Side.LONG and bar.low < scenario.excursion_extreme - invalidation:
            self._expire(bar, "FAILED_AUCTION_EXTREME_RECLAIMED")
            return None
        if self._bar_index > scenario.expiry_index:
            self._expire(bar, "NO_OPPOSITE_DISPLACEMENT_AFTER_REENTRY")
            return None

        body = abs(bar.close - bar.open)
        if scenario.side is Side.SHORT:
            displaced = (
                bar.close < bar.open
                and body >= self.config.min_displacement_atr * atr
                and flow_z <= -self.config.min_reversal_flow_z
                and bar.close < scenario.internal_break
            )
        else:
            displaced = (
                bar.close > bar.open
                and body >= self.config.min_displacement_atr * atr
                and flow_z >= self.config.min_reversal_flow_z
                and bar.close > scenario.internal_break
            )
        if not displaced:
            return None

        structure_overshoot = (
            (scenario.internal_break - bar.close) / atr
            if scenario.side is Side.SHORT
            else (bar.close - scenario.internal_break) / atr
        )
        if structure_overshoot > self.config.max_structure_overshoot_atr:
            self._expire(bar, "REVERSAL_DISPLACEMENT_ALREADY_OVEREXTENDED")
            return None

        previous = scenario.phase
        scenario.phase = Phase.ARMED_REVERSAL
        self._emit(
            scenario_id=scenario.scenario_id,
            event_type="REVERSAL_DISPLACEMENT_CONFIRMED",
            event_time_ns=bar.ts_event_ns,
            previous=previous,
            next_=Phase.ARMED_REVERSAL,
            reason="REENTRY_FOLLOWED_BY_STRUCTURE_BREAK_AND_FLOW_REVERSAL",
            price=bar.close,
            details={
                "flow_z": flow_z,
                "atr": atr,
                "body_atr": body / atr,
                "structure_overshoot_atr": structure_overshoot,
                "internal_break": scenario.internal_break,
                "boundary": scenario.boundary,
                "excursion_extreme": scenario.excursion_extreme,
            },
        )

        if scenario.side is Side.SHORT:
            stop = max(
                scenario.excursion_extreme + self.config.stop_buffer_atr * atr,
                bar.close + self.config.minimum_stop_atr * atr,
            )
            target = scenario.anchor.low
        else:
            stop = min(
                scenario.excursion_extreme - self.config.stop_buffer_atr * atr,
                bar.close - self.config.minimum_stop_atr * atr,
            )
            target = scenario.anchor.high
        return self._build_plan(
            bar=bar,
            scenario=scenario,
            atr=atr,
            stop=stop,
            target=target,
            reason="FAILED_AUCTION_ROTATION_TOWARD_OPPOSING_LIQUIDITY",
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
            self._expire(bar, "OPPOSING_LIQUIDITY_TOO_CLOSE_FOR_STRUCTURAL_PAYOFF")
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
            sweep_extreme=scenario.excursion_extreme,
            atr=atr,
            estimated_reward_risk=reward_risk,
            max_hold_bars=self.config.max_hold_bars,
            reason_code=reason,
        )
        self._emit(
            scenario_id=scenario.scenario_id,
            event_type="TRADE_PLAN_EMITTED",
            event_time_ns=bar.ts_event_ns,
            previous=Phase.ARMED_REVERSAL,
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
                self._watch_for_auction(bar, atr=prior_atr, flow_z=flow_z, volume_z=volume_z)
            elif scenario.phase is Phase.OUTSIDE_TEST:
                plan = self._advance_outside_test(
                    bar,
                    scenario,
                    atr=prior_atr,
                    flow_z=flow_z,
                    volume_z=volume_z,
                )
            elif scenario.phase is Phase.REENTERED:
                plan = self._advance_reentered(bar, scenario, atr=prior_atr, flow_z=flow_z)

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
