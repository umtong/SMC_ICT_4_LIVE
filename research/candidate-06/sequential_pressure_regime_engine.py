"""Causal sequential order-pressure regime continuation.

The state machine detects persistent signed-flow changes with a prior-only robust
CUSUM. A regime is not a trade: the first opposing-flow pullback must fail to
re-enter the regime origin and a separate aligned response must resume. Opposite
CUSUM, origin loss, or age expiry invalidates both pending and live scenarios.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(slots=True)
class _FlowState:
    scenario_id: str
    direction: str
    state: str
    created_index: int
    created_ts_ns: int
    origin: float
    onset_close: float
    midpoint: float
    extreme: float
    atr: float
    onset_score: float
    pullback_index: int | None = None
    pullback_extreme: float | None = None


class SequentialPressureRegimeEngine:
    """Trade only a confirmed continuation inside a live pressure regime."""

    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self._flow_history: list[float] = []
        self._recent_snapshots: list[PrimitiveSnapshot] = []
        self._positive_cusum = 0.0
        self._negative_cusum = 0.0
        self._opposite_cusum = 0.0
        self._state: _FlowState | None = None
        self._sequence = 0
        self._cooldown_until = -1

    @staticmethod
    def _robust_z(value: float, history: list[float]) -> float:
        centre = median(history)
        deviations = [abs(item - centre) for item in history]
        mad = median(deviations)
        if mad <= 1e-9:
            scale = max(1e-3, median([abs(item) for item in history]))
        else:
            scale = 1.4826 * mad
        return max(-6.0, min(6.0, (value - centre) / scale))

    def _transition(
        self,
        state: _FlowState,
        previous: str,
        next_state: str,
        reason: str,
        snapshot: PrimitiveSnapshot,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=state.scenario_id,
            event_type="SPRC_TRANSITION",
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=snapshot.observation.close,
            details={
                "direction": state.direction,
                "origin": state.origin,
                "midpoint": state.midpoint,
                "extreme": state.extreme,
                "onset_score": state.onset_score,
                **dict(details or {}),
            },
        )

    def _reset(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        state = self._state
        if state is None:
            return ScenarioStep()
        transition = self._transition(state, state.state, "RESET", reason, snapshot)
        self._state = None
        self._positive_cusum = 0.0
        self._negative_cusum = 0.0
        self._opposite_cusum = 0.0
        self._cooldown_until = snapshot.index + int(self.params.get("sprc_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,))

    def _maybe_start(
        self,
        snapshot: PrimitiveSnapshot,
        z_score: float,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        if (
            not allow_new
            or self._state is not None
            or snapshot.index < self._cooldown_until
        ):
            return ScenarioStep()
        drift = float(self.params.get("sprc_cusum_drift", 0.25))
        use_cusum = bool(self.params.get("sprc_use_cusum", True))
        self._positive_cusum = max(0.0, self._positive_cusum + z_score - drift)
        self._negative_cusum = max(0.0, self._negative_cusum - z_score - drift)
        threshold = float(self.params.get("sprc_onset_threshold", 4.0))
        if use_cusum:
            long_onset = self._positive_cusum >= threshold
            short_onset = self._negative_cusum >= threshold
            onset_score = max(self._positive_cusum, self._negative_cusum)
        else:
            long_onset = z_score >= threshold
            short_onset = z_score <= -threshold
            onset_score = abs(z_score)
        if not (long_onset or short_onset):
            return ScenarioStep()

        direction = "LONG" if long_onset else "SHORT"
        window = self._recent_snapshots[-int(self.params.get("sprc_onset_window_bars", 5)):]
        if len(window) < 2:
            return ScenarioStep()
        first_close = window[0].observation.close
        price_change = snapshot.observation.close - first_close
        aligned = price_change > 0.0 if direction == "LONG" else price_change < 0.0
        displacement_atr = abs(price_change) / snapshot.atr if snapshot.atr > 0.0 else 0.0
        if (
            not aligned
            or displacement_atr < float(self.params.get("sprc_onset_displacement_atr", 0.35))
        ):
            return ScenarioStep()
        origin = (
            min(item.observation.low for item in window)
            if direction == "LONG"
            else max(item.observation.high for item in window)
        )
        extreme = (
            max(item.observation.high for item in window)
            if direction == "LONG"
            else min(item.observation.low for item in window)
        )
        onset_close = snapshot.observation.close
        self._sequence += 1
        state = _FlowState(
            scenario_id=f"SPRC-{snapshot.observation.ts_ns}-{self._sequence:06d}",
            direction=direction,
            state="PRESSURE_ACTIVE",
            created_index=snapshot.index,
            created_ts_ns=snapshot.observation.ts_ns,
            origin=origin,
            onset_close=onset_close,
            midpoint=(origin + onset_close) / 2.0,
            extreme=extreme,
            atr=snapshot.atr,
            onset_score=onset_score,
        )
        self._state = state
        self._positive_cusum = 0.0
        self._negative_cusum = 0.0
        self._opposite_cusum = 0.0
        return ScenarioStep(
            transitions=(
                self._transition(
                    state,
                    "IDLE",
                    "PRESSURE_ACTIVE",
                    "SEQUENTIAL_SIGNED_FLOW_CHANGE_ESTABLISHED_PRICE_PRESSURE_REGIME",
                    snapshot,
                    {
                        "z_score": z_score,
                        "displacement_atr": displacement_atr,
                        "use_cusum": use_cusum,
                    },
                ),
            ),
        )

    def _build_signal(
        self,
        state: _FlowState,
        snapshot: PrimitiveSnapshot,
    ) -> ScenarioSignal | None:
        observation = snapshot.observation
        entry = observation.close
        buffer = float(self.params.get("sprc_stop_buffer_atr", 0.08)) * state.atr
        projection = float(self.params.get("sprc_projection_fraction", 0.75))
        impulse = max(abs(state.extreme - state.origin), state.atr)
        if state.direction == "LONG":
            stop = min(
                state.midpoint,
                state.pullback_extreme if state.pullback_extreme is not None else state.midpoint,
            ) - buffer
            target = max(state.extreme, entry) + projection * impulse
        else:
            stop = max(
                state.midpoint,
                state.pullback_extreme if state.pullback_extreme is not None else state.midpoint,
            ) + buffer
            target = min(state.extreme, entry) - projection * impulse
        risk = abs(entry - stop)
        reward = target - entry if state.direction == "LONG" else entry - target
        if (
            risk <= 0.0
            or reward <= 0.0
            or reward / risk < float(self.params.get("minimum_structural_rr", 0.75))
        ):
            return None
        return ScenarioSignal(
            scenario_id=state.scenario_id,
            family="SPRC",
            direction=state.direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=entry,
            stop_price=stop,
            target_price=target,
            target_reason="LIVE_PRESSURE_REGIME_RANGE_EXTENSION",
            atr=state.atr,
            liquidity_level=state.midpoint,
            details={
                "pressure_origin": state.origin,
                "pressure_midpoint": state.midpoint,
                "pressure_onset_score": state.onset_score,
                "causal_exit_reason_codes": (
                    "PRESSURE_REGIME_TERMINATED_BY_OPPOSITE_CUSUM",
                    "PRESSURE_REGIME_ORIGIN_LOST",
                    "PRESSURE_REGIME_EXPIRED",
                ),
                "causal_exit_open_position": True,
            },
        )

    def _advance(
        self,
        snapshot: PrimitiveSnapshot,
        z_score: float,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        state = self._state
        if state is None or snapshot.index <= state.created_index:
            return ScenarioStep()
        observation = snapshot.observation
        maximum_age = int(self.params.get("sprc_max_regime_bars", 30))
        if snapshot.index - state.created_index > maximum_age:
            return self._reset(snapshot, "PRESSURE_REGIME_EXPIRED")
        origin_lost = (
            observation.close <= state.origin
            if state.direction == "LONG"
            else observation.close >= state.origin
        )
        if origin_lost:
            return self._reset(snapshot, "PRESSURE_REGIME_ORIGIN_LOST")

        drift = float(self.params.get("sprc_exit_cusum_drift", 0.20))
        opposing = -z_score if state.direction == "LONG" else z_score
        self._opposite_cusum = max(0.0, self._opposite_cusum + opposing - drift)
        if self._opposite_cusum >= float(self.params.get("sprc_exit_threshold", 3.5)):
            return self._reset(snapshot, "PRESSURE_REGIME_TERMINATED_BY_OPPOSITE_CUSUM")

        if state.direction == "LONG":
            state.extreme = max(state.extreme, observation.high)
        else:
            state.extreme = min(state.extreme, observation.low)

        pullback_z = float(self.params.get("sprc_pullback_z", 0.50))
        if state.state == "PRESSURE_ACTIVE":
            counter_flow = z_score <= -pullback_z if state.direction == "LONG" else z_score >= pullback_z
            midpoint_held = observation.close > state.midpoint if state.direction == "LONG" else observation.close < state.midpoint
            if counter_flow and midpoint_held:
                state.state = "PULLBACK_HELD"
                state.pullback_index = snapshot.index
                state.pullback_extreme = observation.low if state.direction == "LONG" else observation.high
                return ScenarioStep(
                    transitions=(
                        self._transition(
                            state,
                            "PRESSURE_ACTIVE",
                            "PULLBACK_HELD",
                            "OPPOSING_FLOW_PULLBACK_FAILED_TO_REENTER_PRESSURE_ORIGIN",
                            snapshot,
                            {"z_score": z_score},
                        ),
                    ),
                )
            return ScenarioStep()

        assert state.state == "PULLBACK_HELD"
        if state.pullback_extreme is not None:
            state.pullback_extreme = (
                min(state.pullback_extreme, observation.low)
                if state.direction == "LONG"
                else max(state.pullback_extreme, observation.high)
            )
        if state.pullback_index is None or snapshot.index <= state.pullback_index:
            return ScenarioStep()
        resume_z = float(self.params.get("sprc_resume_z", 0.50))
        body_atr = abs(observation.close - observation.open) / state.atr if state.atr > 0.0 else 0.0
        body_floor = float(self.params.get("sprc_response_body_atr", 0.15))
        location = float(self.params.get("sprc_response_close_location", 0.62))
        if state.direction == "LONG":
            resumed = (
                z_score >= resume_z
                and observation.close > observation.open
                and body_atr >= body_floor
                and snapshot.close_location >= location
                and observation.close > state.onset_close
            )
        else:
            resumed = (
                z_score <= -resume_z
                and observation.close < observation.open
                and body_atr >= body_floor
                and snapshot.close_location <= 1.0 - location
                and observation.close < state.onset_close
            )
        if not resumed:
            return ScenarioStep()
        transition = self._transition(
            state,
            "PULLBACK_HELD",
            "CONTINUATION_CONFIRMED",
            "PRESSURE_REGIME_PULLBACK_HELD_AND_SEPARATE_FLOW_RESUMPTION_CONFIRMED",
            snapshot,
            {"z_score": z_score},
        )
        signal = self._build_signal(state, snapshot) if allow_new else None
        self._state = None
        self._opposite_cusum = 0.0
        self._cooldown_until = snapshot.index + int(self.params.get("sprc_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,), signal=signal)

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool = True) -> ScenarioStep:
        history_window = int(self.params.get("sprc_flow_history", 120))
        minimum_history = int(self.params.get("sprc_minimum_history", 60))
        history = self._flow_history[-history_window:]
        z_score = self._robust_z(snapshot.flow_ratio, history) if len(history) >= minimum_history else 0.0
        self._recent_snapshots.append(snapshot)
        self._recent_snapshots = self._recent_snapshots[-max(10, int(self.params.get("sprc_onset_window_bars", 5)) + 2):]
        transitions: list[ScenarioTransition] = []
        advanced = self._advance(snapshot, z_score, allow_new=allow_new)
        transitions.extend(advanced.transitions)
        if advanced.signal is not None:
            self._flow_history.append(snapshot.flow_ratio)
            return ScenarioStep(transitions=tuple(transitions), signal=advanced.signal)
        started = self._maybe_start(snapshot, z_score, allow_new=allow_new)
        transitions.extend(started.transitions)
        self._flow_history.append(snapshot.flow_ratio)
        return ScenarioStep(transitions=tuple(transitions))

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        return self._reset(snapshot, reason)
