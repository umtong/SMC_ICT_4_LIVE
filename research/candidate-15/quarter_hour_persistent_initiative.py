#!/usr/bin/env python3
"""Persistent quarter-hour initiative and post-activation continuation logic.

The quarter-hour event is a state observation, never an entry. Two distinct
same-direction common-flow events are required before a market initiative is
active. Each tradable plan then belongs to a new five-minute MSS/displacement/
FVG leg formed after activation. This module contains no fill or account model;
NautilusTrader remains the sole execution and accounting engine.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from math import isfinite
from statistics import fmean
from typing import Any, Mapping

from logic import (
    BarObs,
    Direction,
    LogicConfig,
    MINUTE_NS,
    ResearchEvent,
    Scenario,
    Side,
    StructuralBar,
    TradePlan,
    _TimeAggregator,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
QHI_ROUTER_KEY = "PORTFOLIO::PERSISTENT_QUARTER_HOUR_INITIATIVE"
QHI_MODULE = "PERSISTENT_QH_MSS_FVG_CONTINUATION"
FOUR_HOURS_NS = 240 * MINUTE_NS


@dataclass(frozen=True, slots=True)
class QuarterHourImpulse:
    symbol: str
    start_ts_ns: int
    end_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    atr: float

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def direction(self) -> Direction | None:
        if self.body > 0.0:
            return Direction.LONG
        if self.body < 0.0:
            return Direction.SHORT
        return None

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))

    @property
    def standardized_body(self) -> float:
        return abs(self.body) / max(self.atr, self.close * 1e-12)


@dataclass(frozen=True, slots=True)
class CommonFlowEvent:
    event_id: str
    direction: Direction
    observed_ts_ns: int
    owner_symbol: str
    accepted_symbols: tuple[str, ...]
    origins: Mapping[str, float]
    closes: Mapping[str, float]
    standardized_bodies: Mapping[str, float]
    signed_flows: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class PersistentInitiativeState:
    scenario_id: str
    direction: Direction
    activated_ts_ns: int
    expires_ts_ns: int
    owner_symbol: str
    accepted_symbols: tuple[str, ...]
    origins: Mapping[str, float] = field(default_factory=dict)
    source_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.owner_symbol:
            raise ValueError("initiative identity must not be empty")
        if self.expires_ts_ns <= self.activated_ts_ns:
            raise ValueError("initiative expiry must follow activation")
        if len(self.accepted_symbols) < 3:
            raise ValueError("persistent initiative requires at least three accepted markets")


class PersistentQuarterHourRouter:
    """Convert repeated common-flow events into one observable market state."""

    def __init__(self, config: LogicConfig, instrument_id: str = "PORTFOLIO.GLOBAL") -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self._bars: dict[str, deque[BarObs]] = {
            symbol: deque(maxlen=max(90, config.atr_period + 10))
            for symbol in SYMBOLS
        }
        self._candidate: CommonFlowEvent | None = None
        self._state: PersistentInitiativeState | None = None
        self._event_sequence = 0
        self._initiative_sequence = 0
        self._last_window_end_ns = -1

    @property
    def state(self) -> PersistentInitiativeState | None:
        return self._state

    def _event(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None,
        details: dict[str, Any],
    ) -> None:
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type=event_type,
                event_time_ns=int(event_time_ns),
                observed_time_ns=int(observed_time_ns),
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=(
                    None if reference_price is None else format(reference_price, ".10f")
                ),
                details=details,
            ),
        )

    @staticmethod
    def _true_range(bar: BarObs, previous_close: float | None) -> float:
        if previous_close is None:
            return max(bar.high - bar.low, 1e-12)
        return max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
            1e-12,
        )

    def _atr(self, symbol: str) -> float | None:
        bars = list(self._bars[symbol])
        if len(bars) < self.config.atr_period + 1:
            return None
        sample = bars[-(self.config.atr_period + 1) :]
        ranges = [
            self._true_range(sample[index], sample[index - 1].close)
            for index in range(1, len(sample))
        ]
        return fmean(ranges) if ranges else None

    @staticmethod
    def _is_quarter_hour_window_end(ts_ns: int) -> bool:
        stamp = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        return stamp.second == 0 and stamp.microsecond == 0 and stamp.minute % 15 == 5

    @staticmethod
    def _is_five_minute_boundary(ts_ns: int) -> bool:
        stamp = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        return stamp.second == 0 and stamp.microsecond == 0 and stamp.minute % 5 == 0

    def _impulse(self, symbol: str, ts_ns: int) -> QuarterHourImpulse | None:
        bars = list(self._bars[symbol])
        if len(bars) < max(5, self.config.atr_period + 1):
            return None
        parts = bars[-5:]
        if parts[-1].ts_ns != ts_ns:
            return None
        expected = [parts[0].ts_ns + offset * MINUTE_NS for offset in range(5)]
        if [bar.ts_ns for bar in parts] != expected:
            self.skips["QHI_NONCONTIGUOUS_FIVE_MINUTE_WINDOW"] += 1
            return None
        atr = self._atr(symbol)
        if atr is None or atr <= 0.0:
            return None
        return QuarterHourImpulse(
            symbol=symbol,
            start_ts_ns=parts[0].ts_ns - MINUTE_NS,
            end_ts_ns=parts[-1].ts_ns,
            open=parts[0].open,
            high=max(bar.high for bar in parts),
            low=min(bar.low for bar in parts),
            close=parts[-1].close,
            volume=sum(bar.volume for bar in parts),
            taker_buy_volume=sum(bar.taker_buy_volume for bar in parts),
            atr=atr,
        )

    def _qualified(self, impulse: QuarterHourImpulse, direction: Direction) -> bool:
        directional_body = impulse.body > 0.0 if direction is Direction.LONG else impulse.body < 0.0
        directional_flow = (
            impulse.signed_flow >= self.config.displacement_flow_min
            if direction is Direction.LONG
            else impulse.signed_flow <= -self.config.displacement_flow_min
        )
        return (
            directional_body
            and directional_flow
            and impulse.standardized_body >= self.config.displacement_body_atr
        )

    def _detect_event(self, ts_ns: int) -> CommonFlowEvent | None:
        if not self._is_quarter_hour_window_end(ts_ns) or ts_ns == self._last_window_end_ns:
            return None
        self._last_window_end_ns = ts_ns
        impulses = [self._impulse(symbol, ts_ns) for symbol in SYMBOLS]
        if any(item is None for item in impulses):
            self.skips["QHI_WARMUP_OR_INCOMPLETE_WINDOW"] += 1
            return None
        materialized = [item for item in impulses if item is not None]
        long = [item for item in materialized if self._qualified(item, Direction.LONG)]
        short = [item for item in materialized if self._qualified(item, Direction.SHORT)]
        if len(long) >= 3 and len(short) >= 3:
            self.skips["QHI_AMBIGUOUS_DUAL_DIRECTION_BREADTH"] += 1
            return None
        if len(long) >= 3:
            direction, accepted = Direction.LONG, long
        elif len(short) >= 3:
            direction, accepted = Direction.SHORT, short
        else:
            self.skips["QHI_COMMON_FLOW_BREADTH_BELOW_THREE"] += 1
            return None
        owner = max(
            accepted,
            key=lambda item: (item.standardized_body, abs(item.signed_flow), item.symbol),
        )
        self._event_sequence += 1
        event_id = f"QHE-{ts_ns}-{self._event_sequence:06d}-{direction.value}-{owner.symbol}"
        event = CommonFlowEvent(
            event_id=event_id,
            direction=direction,
            observed_ts_ns=ts_ns,
            owner_symbol=owner.symbol,
            accepted_symbols=tuple(sorted(item.symbol for item in accepted)),
            origins={item.symbol: item.open for item in accepted},
            closes={item.symbol: item.close for item in accepted},
            standardized_bodies={item.symbol: item.standardized_body for item in accepted},
            signed_flows={item.symbol: item.signed_flow for item in accepted},
        )
        self._event(
            scenario_id=event.event_id,
            event_type="QHI_COMMON_FLOW_EVENT_OBSERVED",
            event_time_ns=min(item.start_ts_ns for item in accepted),
            observed_time_ns=ts_ns,
            previous_state="IDLE",
            next_state="OBSERVED",
            reason_code="THREE_MARKET_QUARTER_HOUR_COMMON_FLOW",
            reference_price=owner.close,
            details={
                "direction": direction.value,
                "owner_symbol": owner.symbol,
                "accepted_symbols": list(event.accepted_symbols),
                "origins": dict(event.origins),
                "closes": dict(event.closes),
                "standardized_bodies": dict(event.standardized_bodies),
                "signed_flows": dict(event.signed_flows),
            },
        )
        return event

    def _terminate(self, ts_ns: int, reason: str, details: dict[str, Any]) -> None:
        state = self._state
        if state is None:
            return
        self._event(
            scenario_id=state.scenario_id,
            event_type="QHI_INITIATIVE_TERMINATED",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            previous_state="ACTIVE",
            next_state="TERMINAL",
            reason_code=reason,
            reference_price=None,
            details={
                "direction": state.direction.value,
                "accepted_symbols": list(state.accepted_symbols),
                **details,
            },
        )
        self._state = None

    def _maintain_state(self, ts_ns: int, bars: Mapping[str, BarObs]) -> None:
        if self._candidate is not None and ts_ns - self._candidate.observed_ts_ns > FOUR_HOURS_NS:
            self._candidate = None
        state = self._state
        if state is None:
            return
        if ts_ns >= state.expires_ts_ns:
            self._terminate(ts_ns, "FOUR_HOUR_INFORMATION_HORIZON_EXPIRED", {})
            return
        if not self._is_five_minute_boundary(ts_ns):
            return
        reaccepted: list[str] = []
        for symbol in state.accepted_symbols:
            observation = bars.get(symbol)
            origin = state.origins.get(symbol)
            if observation is None or origin is None:
                continue
            crossed = (
                observation.close <= origin
                if state.direction is Direction.LONG
                else observation.close >= origin
            )
            if crossed:
                reaccepted.append(symbol)
        majority = len(state.accepted_symbols) // 2 + 1
        if len(reaccepted) >= majority:
            self._terminate(
                ts_ns,
                "MAJORITY_CONFIRMING_ORIGINS_REACCEPTED",
                {"reaccepted_symbols": reaccepted, "required_majority": majority},
            )

    def _handle_event(self, event: CommonFlowEvent) -> None:
        current = self._state
        if current is not None and current.direction is not event.direction:
            self._terminate(
                event.observed_ts_ns,
                "OPPOSITE_COMMON_FLOW_EVENT",
                {"opposite_event_id": event.event_id},
            )
        candidate = self._candidate
        if (
            candidate is None
            or candidate.direction is not event.direction
            or event.observed_ts_ns - candidate.observed_ts_ns > FOUR_HOURS_NS
        ):
            self._candidate = event
            return
        if candidate.event_id == event.event_id:
            return

        if self._state is None:
            self._initiative_sequence += 1
            scenario_id = (
                f"QHI-{event.observed_ts_ns}-{self._initiative_sequence:06d}-"
                f"{event.direction.value}"
            )
            state = PersistentInitiativeState(
                scenario_id=scenario_id,
                direction=event.direction,
                activated_ts_ns=event.observed_ts_ns,
                expires_ts_ns=event.observed_ts_ns + FOUR_HOURS_NS,
                owner_symbol=event.owner_symbol,
                accepted_symbols=event.accepted_symbols,
                origins=dict(event.origins),
                source_event_ids=(candidate.event_id, event.event_id),
            )
            self._state = state
            self._event(
                scenario_id=state.scenario_id,
                event_type="QHI_INITIATIVE_ACTIVATED",
                event_time_ns=candidate.observed_ts_ns,
                observed_time_ns=event.observed_ts_ns,
                previous_state="IDLE",
                next_state="ACTIVE",
                reason_code="SECOND_DISTINCT_SAME_DIRECTION_COMMON_FLOW_EVENT",
                reference_price=event.closes.get(event.owner_symbol),
                details={
                    "direction": state.direction.value,
                    "owner_symbol": state.owner_symbol,
                    "accepted_symbols": list(state.accepted_symbols),
                    "origins": dict(state.origins),
                    "source_event_ids": list(state.source_event_ids),
                    "expires_ts_ns": state.expires_ts_ns,
                },
            )
        else:
            state = self._state
            assert state is not None and state.direction is event.direction
            refreshed = replace(
                state,
                expires_ts_ns=event.observed_ts_ns + FOUR_HOURS_NS,
                owner_symbol=event.owner_symbol,
                accepted_symbols=event.accepted_symbols,
                origins=dict(event.origins),
                source_event_ids=tuple((*state.source_event_ids[-3:], event.event_id)),
            )
            self._state = refreshed
            self._event(
                scenario_id=refreshed.scenario_id,
                event_type="QHI_INITIATIVE_REFRESHED",
                event_time_ns=event.observed_ts_ns,
                observed_time_ns=event.observed_ts_ns,
                previous_state="ACTIVE",
                next_state="ACTIVE",
                reason_code="FRESH_SAME_DIRECTION_COMMON_FLOW_EVENT",
                reference_price=event.closes.get(event.owner_symbol),
                details={
                    "owner_symbol": refreshed.owner_symbol,
                    "accepted_symbols": list(refreshed.accepted_symbols),
                    "origins": dict(refreshed.origins),
                    "source_event_id": event.event_id,
                    "expires_ts_ns": refreshed.expires_ts_ns,
                },
            )
        self._candidate = event

    def on_batch(
        self,
        ts_ns: int,
        bars: Mapping[str, BarObs],
    ) -> PersistentInitiativeState | None:
        for symbol in SYMBOLS:
            observation = bars.get(symbol)
            if observation is None:
                self.skips["QHI_SYNCHRONIZED_SYMBOL_MISSING"] += 1
                return self._state
            self._bars[symbol].append(observation)
        self._maintain_state(ts_ns, bars)
        event = self._detect_event(ts_ns)
        if event is not None:
            self._handle_event(event)
        return self._state


@dataclass(frozen=True, slots=True)
class _Pivot:
    known_ts_ns: int
    price: float


class PersistentInitiativeContinuationEngine:
    """Emit independent post-activation five-minute MSS/FVG continuation plans."""

    def __init__(
        self,
        config: LogicConfig,
        instrument_id: str,
        *,
        symbol: str,
        logic_key: str,
    ) -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.symbol = symbol
        self.logic_key = logic_key
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self._aggregate = _TimeAggregator(config.internal_tf_bars)
        self._bars: list[StructuralBar] = []
        self._ranges: deque[float] = deque(maxlen=max(12, config.atr_period))
        self._pivot_highs: list[_Pivot] = []
        self._pivot_lows: list[_Pivot] = []
        self._states: dict[str, str] = {}
        self._active_scenario_id: str | None = None
        self._last_emitted_bar_ns = -1
        self._sequence = 0

    def _event(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None,
        details: dict[str, Any],
    ) -> None:
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type=event_type,
                event_time_ns=int(event_time_ns),
                observed_time_ns=int(observed_time_ns),
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=(
                    None if reference_price is None else format(reference_price, ".10f")
                ),
                details=details,
            ),
        )

    @staticmethod
    def _true_range(bar: StructuralBar, previous_close: float | None) -> float:
        if previous_close is None:
            return bar.span
        return max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )

    def _confirm_pivot(self, known_ts_ns: int) -> None:
        if len(self._bars) < 3:
            return
        left, center, right = self._bars[-3:]
        if center.high > left.high and center.high > right.high:
            self._pivot_highs.append(_Pivot(known_ts_ns, center.high))
        if center.low < left.low and center.low < right.low:
            self._pivot_lows.append(_Pivot(known_ts_ns, center.low))
        if len(self._pivot_highs) > 256:
            del self._pivot_highs[:-128]
        if len(self._pivot_lows) > 256:
            del self._pivot_lows[:-128]

    def _latest_protected(self, direction: Direction, before_ts_ns: int) -> float | None:
        points = self._pivot_highs if direction is Direction.LONG else self._pivot_lows
        for point in reversed(points):
            if point.known_ts_ns < before_ts_ns:
                return point.price
        return None

    def _last_opposing_bar(self, direction: Direction) -> StructuralBar | None:
        for bar in reversed(self._bars[-5:-1]):
            if direction is Direction.LONG and bar.close < bar.open:
                return bar
            if direction is Direction.SHORT and bar.close > bar.open:
                return bar
        return self._bars[-2] if len(self._bars) >= 2 else None

    def _target_pool(
        self,
        *,
        direction: Direction,
        entry: float,
        impulse_close: float,
        atr: float,
        observed_ts_ns: int,
        external_engine: Any,
    ) -> Any | None:
        candidates: list[tuple[float, int, float, Any]] = []
        for pool in getattr(external_engine, "pools", ()):
            if getattr(pool, "consumed", True) or not getattr(pool, "external", False):
                continue
            if int(getattr(pool, "confirmed_ts_ns", observed_ts_ns + 1)) > observed_ts_ns:
                continue
            source = str(getattr(pool, "source", ""))
            if source not in {"COMPLETED_4H_AUCTION", "PREVIOUS_UTC_DAY"}:
                continue
            level = float(getattr(pool, "level"))
            side = getattr(pool, "side")
            if direction is Direction.LONG:
                if side is not Side.HIGH or level <= max(entry, impulse_close):
                    continue
            elif side is not Side.LOW or level >= min(entry, impulse_close):
                continue
            distance = abs(level - entry)
            strength = max(1, int(getattr(pool, "strength", 1)))
            hazard = strength / max(distance / max(atr, 1e-12), 0.20)
            candidates.append((hazard, strength, -distance, pool))
        return max(candidates, default=(0.0, 0, 0.0, None))[-1]

    def _build_plan(
        self,
        *,
        completed: StructuralBar,
        observed_ts_ns: int,
        state: PersistentInitiativeState,
        external_engine: Any,
    ) -> TradePlan | None:
        if len(self._bars) < 3 or len(self._ranges) < self._ranges.maxlen:
            self.skips["QHI_CONTINUATION_WARMUP"] += 1
            return None
        if completed.start_ts_ns <= state.activated_ts_ns:
            self.skips["QHI_CONTINUATION_BAR_NOT_AFTER_ACTIVATION"] += 1
            return None
        if completed.end_ts_ns == self._last_emitted_bar_ns:
            return None
        atr = max(fmean(self._ranges), completed.close * 1e-9)
        direction = state.direction
        protected = self._latest_protected(direction, completed.start_ts_ns)
        if protected is None:
            self.skips["QHI_CONTINUATION_NO_PROTECTED_SWING"] += 1
            return None
        body = abs(completed.close - completed.open)
        close_location = (completed.close - completed.low) / completed.span
        directional_body = (
            completed.close > completed.open
            if direction is Direction.LONG
            else completed.close < completed.open
        )
        directional_flow = (
            completed.signed_flow >= self.config.displacement_flow_min
            if direction is Direction.LONG
            else completed.signed_flow <= -self.config.displacement_flow_min
        )
        structural_break = (
            completed.close > protected
            if direction is Direction.LONG
            else completed.close < protected
        )
        close_extreme = (
            close_location >= self.config.acceptance_close_location
            if direction is Direction.LONG
            else close_location <= 1.0 - self.config.acceptance_close_location
        )
        if not (
            directional_body
            and directional_flow
            and structural_break
            and close_extreme
            and body / atr >= self.config.displacement_body_atr
        ):
            self.skips["QHI_CONTINUATION_MSS_DISPLACEMENT_INCOMPLETE"] += 1
            return None
        first = self._bars[-3]
        if direction is Direction.LONG:
            zone_low, zone_high = first.high, completed.low
        else:
            zone_low, zone_high = completed.high, first.low
        if zone_high <= zone_low:
            self.skips["QHI_CONTINUATION_STRICT_FVG_ABSENT"] += 1
            return None
        opposing = self._last_opposing_bar(direction)
        if opposing is None:
            self.skips["QHI_CONTINUATION_OPPOSING_BAR_ABSENT"] += 1
            return None
        entry = (zone_low + zone_high) / 2.0
        allowance = self.config.stop_buffer_atr * atr
        if direction is Direction.LONG:
            stop = min(opposing.low, protected) - allowance
            causal_entry = stop < entry < completed.close
        else:
            stop = max(opposing.high, protected) + allowance
            causal_entry = completed.close < entry < stop
        if not causal_entry:
            self.skips["QHI_CONTINUATION_NON_CAUSAL_ENTRY_STOP"] += 1
            return None
        target_pool = self._target_pool(
            direction=direction,
            entry=entry,
            impulse_close=completed.close,
            atr=atr,
            observed_ts_ns=observed_ts_ns,
            external_engine=external_engine,
        )
        if target_pool is None:
            self.skips["QHI_CONTINUATION_EXTERNAL_TARGET_ABSENT"] += 1
            return None
        target = float(target_pool.level)
        if direction is Direction.LONG:
            risk = entry - stop
            gross_gain = target - entry
        else:
            risk = stop - entry
            gross_gain = entry - target
        if risk <= 0.0 or gross_gain <= 0.0 or risk / atr < self.config.min_stop_atr:
            self.skips["QHI_CONTINUATION_INVALID_STRUCTURAL_GEOMETRY"] += 1
            return None
        loss = (
            risk
            + entry * self.config.effective_maker_rate
            + stop * self.config.effective_taker_rate
        )
        net_gain = (
            gross_gain
            - entry * self.config.effective_maker_rate
            - target * self.config.effective_maker_rate
        )
        net_r = net_gain / loss if loss > 0.0 else float("-inf")
        if not isfinite(net_r) or net_gain <= 0.0 or net_r < self.config.min_net_r:
            self.skips["QHI_CONTINUATION_INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
            return None
        self._sequence += 1
        scenario_id = (
            f"QHC-{self.symbol}-{completed.end_ts_ns}-"
            f"{self._sequence:06d}-{direction.value}"
        )
        expire_ts_ns = (
            observed_ts_ns
            + self.config.retrace_expiry_bars
            * self.config.internal_tf_bars
            * MINUTE_NS
        )
        details = {
            "_logic_key": self.logic_key,
            "module": QHI_MODULE,
            "route": "PERSISTENT_QUARTER_HOUR_INITIATIVE_MSS_FVG",
            "initiative_id": state.scenario_id,
            "initiative_direction": state.direction.value,
            "initiative_activated_ts_ns": state.activated_ts_ns,
            "initiative_expires_ts_ns": state.expires_ts_ns,
            "initiative_owner_symbol": state.owner_symbol,
            "initiative_source_event_ids": list(state.source_event_ids),
            "protected_swing": protected,
            "mss_bar_start_ts_ns": completed.start_ts_ns,
            "mss_bar_end_ts_ns": completed.end_ts_ns,
            "mss_body_atr": body / atr,
            "mss_signed_flow": completed.signed_flow,
            "fvg_low": zone_low,
            "fvg_high": zone_high,
            "entry_model": "FIRST_FVG_CONSEQUENT_ENCROACHMENT",
            "stop_model": "LAST_OPPOSING_BAR_OR_PROTECTED_SWING",
            "target_model": "NEXT_LIVE_EXTERNAL_4H_OR_PREVIOUS_DAY_POOL",
            "target_pool_id": str(target_pool.scenario_id),
            "target_pool_source": str(target_pool.source),
            "entry_cost_assumption": "MAKER",
            "stop_cost_assumption": "TAKER",
            "target_cost_assumption": "MAKER",
            "independent_episode_key": f"{state.scenario_id}:{self.symbol}:{completed.end_ts_ns}",
        }
        plan = TradePlan(
            scenario_id=scenario_id,
            scenario=Scenario.AAC,
            direction=direction,
            observed_ts_ns=observed_ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            atr=atr,
            loss_per_unit=loss,
            gain_per_unit=net_gain,
            net_r=net_r,
            reason_code="PERSISTENT_QH_INITIATIVE_MSS_FVG_RETEST",
            expire_ts_ns=expire_ts_ns,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details=details,
        )
        self._states[scenario_id] = "PENDING_ENTRY"
        self._last_emitted_bar_ns = completed.end_ts_ns
        self._event(
            scenario_id=scenario_id,
            event_type="QHI_CONTINUATION_PLAN_CONFIRMED",
            event_time_ns=completed.start_ts_ns,
            observed_time_ns=observed_ts_ns,
            previous_state="IDLE",
            next_state="PENDING_ENTRY",
            reason_code=plan.reason_code,
            reference_price=entry,
            details={
                "direction": direction.value,
                "entry": entry,
                "stop": stop,
                "target": target,
                "net_r": net_r,
                **details,
            },
        )
        return plan

    def on_bar(
        self,
        observation: BarObs,
        *,
        state: PersistentInitiativeState | None,
        external_engine: Any,
    ) -> TradePlan | None:
        completed = self._aggregate.update(observation)
        if completed is None:
            return None
        previous_close = self._bars[-1].close if self._bars else None
        self._ranges.append(self._true_range(completed, previous_close))
        self._bars.append(completed)
        if len(self._bars) > 512:
            del self._bars[:-384]
        plan = None
        if state is None:
            self.skips["QHI_CONTINUATION_WITHOUT_ACTIVE_INITIATIVE"] += 1
        elif observation.ts_ns >= state.expires_ts_ns:
            self.skips["QHI_CONTINUATION_INITIATIVE_EXPIRED"] += 1
        else:
            plan = self._build_plan(
                completed=completed,
                observed_ts_ns=observation.ts_ns,
                state=state,
                external_engine=external_engine,
            )
        self._confirm_pivot(observation.ts_ns)
        return plan

    @staticmethod
    def _ts(value: Any) -> int:
        return int(getattr(value, "ts_ns", value))

    def _transition(
        self,
        plan: TradePlan,
        *,
        ts_ns: int,
        next_state: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        previous = self._states.get(plan.scenario_id)
        if previous is None or previous == "TERMINAL":
            return
        self._event(
            scenario_id=plan.scenario_id,
            event_type="QHI_CONTINUATION_LIFECYCLE",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=plan.expected_entry,
            details=details or {},
        )
        self._states[plan.scenario_id] = next_state

    def mark_rejected(
        self,
        plan: TradePlan,
        ts_or_bar: Any,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._transition(
            plan,
            ts_ns=self._ts(ts_or_bar),
            next_state="TERMINAL",
            reason=reason,
            details=details,
        )

    def mark_submitted(
        self,
        plan: TradePlan,
        quantity: Any,
        details: dict[str, Any],
    ) -> None:
        payload = dict(details)
        payload.update({"quantity": str(quantity), "module": QHI_MODULE})
        self._transition(
            plan,
            ts_ns=plan.observed_ts_ns,
            next_state="SUBMITTED",
            reason="NAUTILUS_BRACKET_SUBMITTED",
            details=payload,
        )
        self._active_scenario_id = plan.scenario_id

    def mark_entry_filled(self, ts_ns: int, details: dict[str, Any]) -> None:
        scenario_id = str(details.get("scenario_id", self._active_scenario_id or ""))
        if self._states.get(scenario_id) != "SUBMITTED":
            return
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type="QHI_CONTINUATION_ENTRY_FILLED",
                event_time_ns=int(ts_ns),
                observed_time_ns=int(ts_ns),
                previous_state="SUBMITTED",
                next_state="POSITION_OPEN",
                reason_code="NAUTILUS_PARENT_FILLED",
                reference_price=None,
                details=dict(details),
            ),
        )
        self._states[scenario_id] = "POSITION_OPEN"
        self._active_scenario_id = scenario_id

    def mark_trade_terminal(self, ts_ns: int, reason: str) -> None:
        scenario_id = self._active_scenario_id
        if not scenario_id:
            return
        previous = self._states.get(scenario_id)
        if previous not in {"SUBMITTED", "POSITION_OPEN"}:
            return
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type="QHI_CONTINUATION_TERMINAL",
                event_time_ns=int(ts_ns),
                observed_time_ns=int(ts_ns),
                previous_state=previous,
                next_state="TERMINAL",
                reason_code=reason,
                reference_price=None,
                details={"module": QHI_MODULE},
            ),
        )
        self._states[scenario_id] = "TERMINAL"
        self._active_scenario_id = None
