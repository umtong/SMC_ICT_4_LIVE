"""Structural stop/target selection and signal arming for candidate-06."""

from __future__ import annotations

from lrb_types import PrimitiveSnapshot, ScenarioStep, _Episode


class ScenarioArmMixin:
    """Arm a structurally confirmed reversal or continuation."""

    def _arm_reversal(self, snapshot: PrimitiveSnapshot, episode: _Episode) -> ScenarioStep:
        buffer_value = float(self.params["stop_buffer_atr"]) * snapshot.atr
        if episode.direction == "SHORT":
            stop = episode.extreme + buffer_value
            candidates = [
                (snapshot.lower_fast, "OPPOSING_INTERNAL_LIQUIDITY"),
                (snapshot.lower_slow, "OPPOSING_EXTERNAL_LIQUIDITY"),
            ]
        else:
            stop = episode.extreme - buffer_value
            candidates = [
                (snapshot.upper_fast, "OPPOSING_INTERNAL_LIQUIDITY"),
                (snapshot.upper_slow, "OPPOSING_EXTERNAL_LIQUIDITY"),
            ]
        target = self._select_target(
            direction=episode.direction,
            entry=snapshot.observation.close,
            stop=stop,
            candidates=candidates,
        )
        if target is None:
            return self._reset(snapshot, "NO_OPPOSING_LIQUIDITY_WITH_SUFFICIENT_SPACE")
        return self._emit_signal(snapshot, episode, stop, target[0], target[1], "REJECTION_CONFIRMED")

    def _arm_continuation(self, snapshot: PrimitiveSnapshot, episode: _Episode) -> ScenarioStep:
        buffer_value = float(self.params["stop_buffer_atr"]) * snapshot.atr
        obs = snapshot.observation
        if episode.direction == "LONG":
            stop = min(obs.low, episode.level - buffer_value)
        else:
            stop = max(obs.high, episode.level + buffer_value)
        target = self._continuation_target(snapshot, episode, entry=obs.close, stop=stop)
        if target is None:
            return self._reset(snapshot, "NO_CONTINUATION_OBJECTIVE_WITH_SUFFICIENT_SPACE")
        return self._emit_signal(snapshot, episode, stop, target[0], target[1], "ACCEPTANCE_RETEST_HELD")

