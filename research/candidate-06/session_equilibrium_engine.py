"""Session displacement-retest entries with internal/equilibrium target hierarchy."""

from __future__ import annotations

from lrb_types import PrimitiveSnapshot, ScenarioStep
from session_displacement_engine import SessionDisplacementRetestEngine
from session_engine import _SessionEpisode


class SessionEquilibriumRetestEngine(SessionDisplacementRetestEngine):
    """Keep v0.4 entry causality; replace only the structural objective hierarchy.

    A failed auction need not traverse the entire completed range.  The nearest
    prior internal liquidity or range equilibrium which still clears the fixed
    after-entry structural RR is selected before the opposite external boundary.
    """

    def _arm_reversal(self, snapshot: PrimitiveSnapshot, episode: _SessionEpisode) -> ScenarioStep:
        buffer_value = float(self.params.get("stop_buffer_atr", 0.10)) * snapshot.atr
        observation = snapshot.observation
        session_mid = None
        if episode.range_high is not None and episode.range_low is not None:
            session_mid = (episode.range_high + episode.range_low) / 2.0

        if episode.direction == "SHORT":
            stop = episode.extreme + buffer_value
            candidates = [
                (snapshot.lower_fast, "PRIOR_INTERNAL_SELLSIDE_LIQUIDITY"),
                (session_mid, "SESSION_DEALING_RANGE_EQUILIBRIUM"),
                (episode.range_low, "OPPOSITE_SESSION_RANGE_LIQUIDITY"),
                (self._asia_low, "ASIA_LOW_LIQUIDITY"),
                (self._previous_day_low, "PREVIOUS_DAY_LOW_LIQUIDITY"),
            ]
        else:
            stop = episode.extreme - buffer_value
            candidates = [
                (snapshot.upper_fast, "PRIOR_INTERNAL_BUYSIDE_LIQUIDITY"),
                (session_mid, "SESSION_DEALING_RANGE_EQUILIBRIUM"),
                (episode.range_high, "OPPOSITE_SESSION_RANGE_LIQUIDITY"),
                (self._asia_high, "ASIA_HIGH_LIQUIDITY"),
                (self._previous_day_high, "PREVIOUS_DAY_HIGH_LIQUIDITY"),
            ]
        target = self._select_target(
            episode.direction,
            observation.close,
            stop,
            candidates,
        )
        if target is None:
            return self._reset(snapshot, episode, "NO_INTERNAL_OR_EXTERNAL_LIQUIDITY_WITH_SUFFICIENT_SPACE")
        return self._emit(
            snapshot,
            episode,
            stop,
            target[0],
            target[1],
            "SESSION_DISPLACEMENT_RETRACE_REJECTED",
        )
