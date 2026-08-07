"""Prior-only robust sequential pressure state without entry generation."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from lrb_types import PrimitiveSnapshot, ScenarioTransition


@dataclass(slots=True)
class PressureState:
    scenario_id: str
    direction: str
    created_index: int
    created_ts_ns: int
    origin: float
    onset_close: float
    onset_score: float


class PressureStateTracker:
    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self.history: list[float] = []
        self.recent: list[PrimitiveSnapshot] = []
        self.positive = 0.0
        self.negative = 0.0
        self.opposite = 0.0
        self.state: PressureState | None = None
        self.sequence = 0

    @staticmethod
    def _z(value: float, history: list[float]) -> float:
        centre = median(history)
        mad = median([abs(item - centre) for item in history])
        scale = 1.4826 * mad if mad > 1e-9 else max(1e-3, median([abs(item) for item in history]))
        return max(-6.0, min(6.0, (value - centre) / scale))

    def _transition(
        self,
        state: PressureState,
        previous: str,
        next_state: str,
        reason: str,
        snapshot: PrimitiveSnapshot,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=state.scenario_id,
            event_type="PRESSURE_STATE_TRANSITION",
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=snapshot.observation.close,
            details={
                "direction": state.direction,
                "origin": state.origin,
                "onset_close": state.onset_close,
                "onset_score": state.onset_score,
                **dict(details or {}),
            },
        )

    def update(self, snapshot: PrimitiveSnapshot) -> tuple[ScenarioTransition, ...]:
        history = self.history[-int(self.params.get("phml_flow_history", 120)):]
        minimum = int(self.params.get("phml_minimum_history", 60))
        z_score = self._z(snapshot.flow_ratio, history) if len(history) >= minimum else 0.0
        self.recent.append(snapshot)
        window_bars = int(self.params.get("phml_onset_window_bars", 5))
        self.recent = self.recent[-max(10, window_bars + 2):]
        transitions: list[ScenarioTransition] = []
        state = self.state
        if state is not None:
            observation = snapshot.observation
            if snapshot.index - state.created_index > int(self.params.get("phml_max_regime_bars", 30)):
                transitions.append(
                    self._transition(state, "PRESSURE_ACTIVE", "RESET", "PRESSURE_REGIME_EXPIRED", snapshot),
                )
                self.state = None
                self.opposite = 0.0
            else:
                origin_lost = (
                    observation.close <= state.origin
                    if state.direction == "LONG"
                    else observation.close >= state.origin
                )
                if origin_lost:
                    transitions.append(
                        self._transition(state, "PRESSURE_ACTIVE", "RESET", "PRESSURE_REGIME_ORIGIN_LOST", snapshot),
                    )
                    self.state = None
                    self.opposite = 0.0
                else:
                    drift = float(self.params.get("phml_exit_cusum_drift", 0.20))
                    opposing = -z_score if state.direction == "LONG" else z_score
                    self.opposite = max(0.0, self.opposite + opposing - drift)
                    if self.opposite >= float(self.params.get("phml_exit_threshold", 3.5)):
                        transitions.append(
                            self._transition(
                                state,
                                "PRESSURE_ACTIVE",
                                "RESET",
                                "PRESSURE_REGIME_TERMINATED_BY_OPPOSITE_CUSUM",
                                snapshot,
                                {"opposite_cusum": self.opposite},
                            ),
                        )
                        self.state = None
                        self.opposite = 0.0

        if self.state is None and len(history) >= minimum:
            drift = float(self.params.get("phml_cusum_drift", 0.25))
            self.positive = max(0.0, self.positive + z_score - drift)
            self.negative = max(0.0, self.negative - z_score - drift)
            threshold = float(self.params.get("phml_onset_threshold", 4.0))
            long_onset = self.positive >= threshold
            short_onset = self.negative >= threshold
            if long_onset or short_onset:
                direction = "LONG" if long_onset else "SHORT"
                window = self.recent[-window_bars:]
                first_close = window[0].observation.close if window else snapshot.observation.close
                price_change = snapshot.observation.close - first_close
                aligned = price_change > 0.0 if direction == "LONG" else price_change < 0.0
                displacement = abs(price_change) / snapshot.atr if snapshot.atr > 0.0 else 0.0
                if aligned and displacement >= float(self.params.get("phml_onset_displacement_atr", 0.35)):
                    origin = (
                        min(item.observation.low for item in window)
                        if direction == "LONG"
                        else max(item.observation.high for item in window)
                    )
                    self.sequence += 1
                    state = PressureState(
                        scenario_id=f"PHML-PRESSURE-{snapshot.observation.ts_ns}-{self.sequence:06d}",
                        direction=direction,
                        created_index=snapshot.index,
                        created_ts_ns=snapshot.observation.ts_ns,
                        origin=origin,
                        onset_close=snapshot.observation.close,
                        onset_score=max(self.positive, self.negative),
                    )
                    self.state = state
                    self.positive = 0.0
                    self.negative = 0.0
                    self.opposite = 0.0
                    transitions.append(
                        self._transition(
                            state,
                            "IDLE",
                            "PRESSURE_ACTIVE",
                            "SEQUENTIAL_SIGNED_FLOW_CHANGE_ESTABLISHED_PRICE_PRESSURE_REGIME",
                            snapshot,
                            {"z_score": z_score, "displacement_atr": displacement},
                        ),
                    )
        self.history.append(snapshot.flow_ratio)
        return tuple(transitions)

    @property
    def active_direction(self) -> str | None:
        return None if self.state is None else self.state.direction
