"""Open-interest contraction exhaustion/continuation bifurcation.

A completed five-minute OI contraction is an initiating deleveraging shock, not
an entry. Later completed one-minute bars must either reclaim the shock midpoint
(exhaustion) or defend it and resume (continuation).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import log
from statistics import median
from typing import Any, Mapping

from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition
from open_interest_metrics_data import OpenInterestPoint


@dataclass(slots=True)
class _Shock:
    scenario_id: str
    direction: str
    state: str
    started_index: int
    started_ts_ns: int
    start_price: float
    close_price: float
    high: float
    low: float
    midpoint: float
    atr: float
    oi_log_change: float
    oi_score: float
    interval_flow: float
    retrace_extreme: float | None = None
    retrace_index: int | None = None


class OpenInterestContractionBifurcationEngine:
    def __init__(self, params: Mapping[str, Any], *, open_interest: Mapping[int, OpenInterestPoint]):
        self.params = dict(params)
        self._open_interest = dict(open_interest)
        self._previous_point: OpenInterestPoint | None = None
        self._previous_close: float | None = None
        self._oi_changes: list[float] = []
        self._interval_open: float | None = None
        self._interval_high: float | None = None
        self._interval_low: float | None = None
        self._interval_volume = 0.0
        self._interval_signed_volume = 0.0
        self._shock: _Shock | None = None
        self._sequence = 0
        self._cooldown_until = -1

    def _transition(
        self,
        shock: _Shock,
        previous: str,
        next_state: str,
        reason: str,
        snapshot: PrimitiveSnapshot,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=shock.scenario_id,
            event_type="OICB_TRANSITION",
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=snapshot.observation.close,
            details={
                "direction": shock.direction,
                "oi_log_change": shock.oi_log_change,
                "oi_score": shock.oi_score,
                "interval_flow": shock.interval_flow,
                **dict(details or {}),
            },
        )

    @staticmethod
    def _robust_score(value: float, history: list[float]) -> float:
        centre = median(history)
        mad = median([abs(item - centre) for item in history])
        if mad <= 1e-12:
            return 0.0 if value >= centre else -99.0
        return (value - centre) / (1.4826 * mad)

    def _accumulate(self, snapshot: PrimitiveSnapshot) -> None:
        observation = snapshot.observation
        if self._interval_open is None:
            self._interval_open = observation.open
            self._interval_high = observation.high
            self._interval_low = observation.low
        else:
            self._interval_high = max(float(self._interval_high), observation.high)
            self._interval_low = min(float(self._interval_low), observation.low)
        self._interval_volume += observation.volume
        self._interval_signed_volume += snapshot.flow_ratio * observation.volume

    def _reset_interval(self, close: float) -> None:
        self._interval_open = close
        self._interval_high = close
        self._interval_low = close
        self._interval_volume = 0.0
        self._interval_signed_volume = 0.0

    def _maybe_start_shock(
        self,
        snapshot: PrimitiveSnapshot,
        *,
        allow_new: bool,
    ) -> tuple[ScenarioTransition, ...]:
        point = self._open_interest.get(snapshot.observation.ts_ns)
        if point is None:
            return ()
        previous_point = self._previous_point
        previous_close = self._previous_close
        self._previous_point = point
        self._previous_close = snapshot.observation.close
        if previous_point is None or previous_close is None:
            self._reset_interval(snapshot.observation.close)
            return ()

        oi_change = log(point.open_interest / previous_point.open_interest)
        history_points = int(self.params.get("oicb_history_points", 72))
        minimum_history = int(self.params.get("oicb_minimum_history", 36))
        history = self._oi_changes[-history_points:]
        score = self._robust_score(oi_change, history) if len(history) >= minimum_history else 0.0
        self._oi_changes.append(oi_change)

        interval_open = float(self._interval_open if self._interval_open is not None else previous_close)
        interval_high = float(self._interval_high if self._interval_high is not None else snapshot.observation.high)
        interval_low = float(self._interval_low if self._interval_low is not None else snapshot.observation.low)
        interval_flow = (
            self._interval_signed_volume / self._interval_volume
            if self._interval_volume > 0.0
            else 0.0
        )
        self._reset_interval(snapshot.observation.close)

        if not allow_new or self._shock is not None or snapshot.index < self._cooldown_until:
            return ()
        use_oi = bool(self.params.get("oicb_use_open_interest", True))
        use_flow = bool(self.params.get("oicb_require_aligned_flow", True))
        drop_floor = float(self.params.get("oicb_minimum_drop_fraction", 0.0010))
        score_floor = float(self.params.get("oicb_score_floor", 1.5))
        oi_extreme = (-oi_change >= drop_floor and score <= -score_floor) if use_oi else True
        price_change = snapshot.observation.close - interval_open
        direction = "UP" if price_change > 0.0 else "DOWN"
        price_atr = abs(price_change) / snapshot.atr if snapshot.atr > 0.0 else 0.0
        flow_floor = float(self.params.get("oicb_flow_floor", 0.04))
        flow_aligned = interval_flow >= flow_floor if direction == "UP" else interval_flow <= -flow_floor
        if (
            not oi_extreme
            or price_atr < float(self.params.get("oicb_price_move_atr", 0.50))
            or (use_flow and not flow_aligned)
        ):
            return ()

        self._sequence += 1
        shock = _Shock(
            scenario_id=f"OICB-{snapshot.observation.ts_ns}-{self._sequence:06d}",
            direction=direction,
            state="SHOCK_CONFIRMED",
            started_index=snapshot.index,
            started_ts_ns=snapshot.observation.ts_ns,
            start_price=interval_open,
            close_price=snapshot.observation.close,
            high=interval_high,
            low=interval_low,
            midpoint=(interval_high + interval_low) / 2.0,
            atr=snapshot.atr,
            oi_log_change=oi_change,
            oi_score=score,
            interval_flow=interval_flow,
        )
        self._shock = shock
        return (
            self._transition(
                shock,
                "IDLE",
                "SHOCK_CONFIRMED",
                "COMPLETED_OI_CONTRACTION_WITH_DIRECTIONAL_PRICE_IMPACT",
                snapshot,
                {"price_move_atr": price_atr},
            ),
        )

    def _signal(
        self,
        shock: _Shock,
        snapshot: PrimitiveSnapshot,
        *,
        branch: str,
    ) -> ScenarioSignal | None:
        close = snapshot.observation.close
        buffer = float(self.params.get("oicb_stop_buffer_atr", 0.08)) * shock.atr
        projection = float(self.params.get("oicb_projection_fraction", 0.75))
        if branch == "EXHAUSTION":
            if shock.direction == "UP":
                direction, stop, target = "SHORT", shock.high + buffer, shock.start_price
            else:
                direction, stop, target = "LONG", shock.low - buffer, shock.start_price
            family = "OICB_E"
            target_reason = "PRE_SHOCK_PRICE"
        else:
            if shock.direction == "UP":
                direction = "LONG"
                stop = min(
                    shock.midpoint,
                    shock.retrace_extreme if shock.retrace_extreme is not None else shock.midpoint,
                ) - buffer
                target = shock.high + projection * max(shock.high - shock.low, shock.atr)
            else:
                direction = "SHORT"
                stop = max(
                    shock.midpoint,
                    shock.retrace_extreme if shock.retrace_extreme is not None else shock.midpoint,
                ) + buffer
                target = shock.low - projection * max(shock.high - shock.low, shock.atr)
            family = "OICB_C"
            target_reason = "SHOCK_RANGE_EXTENSION"
        risk = abs(close - stop)
        reward = target - close if direction == "LONG" else close - target
        if (
            risk <= 0.0
            or reward <= 0.0
            or reward / risk < float(self.params.get("minimum_structural_rr", 0.75))
        ):
            return None
        return ScenarioSignal(
            scenario_id=shock.scenario_id,
            family=family,
            direction=direction,
            observed_ts_ns=snapshot.observation.ts_ns,
            reference_entry=close,
            stop_price=stop,
            target_price=target,
            target_reason=target_reason,
            atr=shock.atr,
            liquidity_level=shock.midpoint,
            details={
                "shock_direction": shock.direction,
                "oi_log_change": shock.oi_log_change,
                "oi_score": shock.oi_score,
                "shock_start_price": shock.start_price,
                "shock_high": shock.high,
                "shock_low": shock.low,
                "shock_midpoint": shock.midpoint,
            },
        )

    def _advance_shock(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        shock = self._shock
        if shock is None or snapshot.index <= shock.started_index:
            return ScenarioStep()
        transitions: list[ScenarioTransition] = []
        if snapshot.index - shock.started_index > int(self.params.get("oicb_response_bars", 12)):
            transitions.append(
                self._transition(
                    shock,
                    shock.state,
                    "RESET",
                    "OI_CONTRACTION_RESPONSE_EXPIRED",
                    snapshot,
                ),
            )
            self._shock = None
            self._cooldown_until = snapshot.index + int(self.params.get("oicb_cooldown_bars", 2))
            return ScenarioStep(transitions=tuple(transitions))

        observation = snapshot.observation
        body_atr = abs(observation.close - observation.open) / shock.atr if shock.atr > 0.0 else 0.0
        body_floor = float(self.params.get("oicb_response_body_atr", 0.15))
        flow_floor = float(self.params.get("oicb_response_flow_ratio", 0.03))
        location_floor = float(self.params.get("oicb_response_close_location", 0.62))
        if shock.direction == "UP":
            exhaustion = (
                observation.close < shock.midpoint
                and observation.close < observation.open
                and body_atr >= body_floor
                and snapshot.flow_ratio <= -flow_floor
                and snapshot.close_location <= 1.0 - location_floor
            )
            held = observation.low <= shock.close_price and observation.close > shock.midpoint
            resumed = (
                shock.state == "CONTINUATION_RETEST"
                and observation.close > observation.open
                and body_atr >= body_floor
                and snapshot.flow_ratio >= flow_floor
                and snapshot.close_location >= location_floor
            )
        else:
            exhaustion = (
                observation.close > shock.midpoint
                and observation.close > observation.open
                and body_atr >= body_floor
                and snapshot.flow_ratio >= flow_floor
                and snapshot.close_location >= location_floor
            )
            held = observation.high >= shock.close_price and observation.close < shock.midpoint
            resumed = (
                shock.state == "CONTINUATION_RETEST"
                and observation.close < observation.open
                and body_atr >= body_floor
                and snapshot.flow_ratio <= -flow_floor
                and snapshot.close_location <= 1.0 - location_floor
            )

        if exhaustion:
            transitions.append(
                self._transition(
                    shock,
                    shock.state,
                    "EXHAUSTION_CONFIRMED",
                    "OI_CONTRACTION_PRICE_IMPACT_EXHAUSTED_AND_MIDPOINT_RECLAIMED",
                    snapshot,
                ),
            )
            signal = self._signal(shock, snapshot, branch="EXHAUSTION") if allow_new else None
            self._shock = None
            self._cooldown_until = snapshot.index + int(self.params.get("oicb_cooldown_bars", 2))
            return ScenarioStep(transitions=tuple(transitions), signal=signal)

        if shock.state == "SHOCK_CONFIRMED" and held:
            shock.state = "CONTINUATION_RETEST"
            shock.retrace_extreme = observation.low if shock.direction == "UP" else observation.high
            shock.retrace_index = snapshot.index
            transitions.append(
                self._transition(
                    shock,
                    "SHOCK_CONFIRMED",
                    "CONTINUATION_RETEST",
                    "OI_CONTRACTION_SHOCK_MIDPOINT_DEFENDED_ON_RETEST",
                    snapshot,
                ),
            )
            return ScenarioStep(transitions=tuple(transitions))

        if resumed and shock.retrace_index is not None and snapshot.index > shock.retrace_index:
            transitions.append(
                self._transition(
                    shock,
                    "CONTINUATION_RETEST",
                    "CONTINUATION_CONFIRMED",
                    "OI_CONTRACTION_RETEST_HELD_AND_SEPARATE_RESUMPTION_CONFIRMED",
                    snapshot,
                ),
            )
            signal = self._signal(shock, snapshot, branch="CONTINUATION") if allow_new else None
            self._shock = None
            self._cooldown_until = snapshot.index + int(self.params.get("oicb_cooldown_bars", 2))
            return ScenarioStep(transitions=tuple(transitions), signal=signal)

        if shock.state == "CONTINUATION_RETEST" and shock.retrace_extreme is not None:
            if shock.direction == "UP":
                shock.retrace_extreme = min(shock.retrace_extreme, observation.low)
            else:
                shock.retrace_extreme = max(shock.retrace_extreme, observation.high)
        return ScenarioStep(transitions=tuple(transitions))

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool = True) -> ScenarioStep:
        self._accumulate(snapshot)
        transitions = list(self._maybe_start_shock(snapshot, allow_new=allow_new))
        advanced = self._advance_shock(snapshot, allow_new=allow_new)
        transitions.extend(advanced.transitions)
        return ScenarioStep(transitions=tuple(transitions), signal=advanced.signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        shock = self._shock
        if shock is None:
            return ScenarioStep()
        transition = self._transition(shock, shock.state, "RESET", reason, snapshot)
        self._shock = None
        return ScenarioStep(transitions=(transition,))
