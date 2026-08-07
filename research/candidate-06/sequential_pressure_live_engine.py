"""Live-context preservation for sequential pressure regimes."""
from __future__ import annotations

from lrb_types import PrimitiveSnapshot, ScenarioStep
from sequential_pressure_regime_engine import SequentialPressureRegimeEngine


class SequentialPressureLiveEngine(SequentialPressureRegimeEngine):
    """Keep a confirmed regime alive so its declared exit codes remain observable."""

    def _advance(
        self,
        snapshot: PrimitiveSnapshot,
        z_score: float,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        state = self._state
        if state is not None and state.state == "POSITION_CONTEXT":
            observation = snapshot.observation
            if snapshot.index - state.created_index > int(
                self.params.get("sprc_max_regime_bars", 30),
            ):
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
            self._opposite_cusum = max(
                0.0,
                self._opposite_cusum + opposing - drift,
            )
            if self._opposite_cusum >= float(
                self.params.get("sprc_exit_threshold", 3.5),
            ):
                return self._reset(
                    snapshot,
                    "PRESSURE_REGIME_TERMINATED_BY_OPPOSITE_CUSUM",
                )
            if state.direction == "LONG":
                state.extreme = max(state.extreme, observation.high)
            else:
                state.extreme = min(state.extreme, observation.low)
            return ScenarioStep()

        state_before = self._state
        step = super()._advance(snapshot, z_score, allow_new=allow_new)
        if step.signal is not None and state_before is not None:
            state_before.state = "POSITION_CONTEXT"
            self._state = state_before
            self._opposite_cusum = 0.0
        return step
