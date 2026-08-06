"""Completed-hour auction with explicit structural continuation invalidation."""

from __future__ import annotations

from typing import Any, Mapping

from auction_relay_engine import RollingAuctionLiquidityRelayEngine
from lrb_types import PrimitiveSnapshot, ScenarioStep
from session_engine import _SessionEpisode


class RollingAuctionStructuralStopEngine(RollingAuctionLiquidityRelayEngine):
    """Compare retest-boundary stops with the acceptance impulse origin.

    The acceptance bar is the displacement which first demonstrates that the
    completed auction boundary was accepted.  In impulse-origin mode the
    continuation thesis is invalidated only beyond the lows/highs of both that
    displacement and its first retest, plus the pre-existing structural buffer.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._acceptance_origins: dict[str, tuple[float, float]] = {}

    def _classify_response(
        self,
        snapshot: PrimitiveSnapshot,
        episode: _SessionEpisode,
    ) -> ScenarioStep:
        was_pending = episode.family == "PENDING"
        step = super()._classify_response(snapshot, episode)
        if was_pending and self._episode is episode and episode.family == "SAC":
            self._acceptance_origins[episode.scenario_id] = (
                float(snapshot.observation.low),
                float(snapshot.observation.high),
            )
        return step

    def _arm_continuation(
        self,
        snapshot: PrimitiveSnapshot,
        episode: _SessionEpisode,
    ) -> ScenarioStep:
        mode = str(self.params.get("continuation_stop_mode", "RETEST_BOUNDARY")).upper()
        if mode == "RETEST_BOUNDARY":
            return super()._arm_continuation(snapshot, episode)
        if mode != "ACCEPTANCE_IMPULSE_ORIGIN":
            raise ValueError(f"unsupported continuation_stop_mode: {mode}")

        origin = self._acceptance_origins.get(episode.scenario_id)
        if origin is None:
            return self._reset(snapshot, episode, "ACCEPTANCE_IMPULSE_ORIGIN_MISSING")
        acceptance_low, acceptance_high = origin
        observation = snapshot.observation
        buffer_value = float(self.params.get("stop_buffer_atr", 0.10)) * snapshot.atr
        width = None
        if episode.range_high is not None and episode.range_low is not None:
            width = episode.range_high - episode.range_low
        if width is None or width <= 0.0:
            width = snapshot.atr * float(self.params.get("session_projection_atr", 3.0))
        projection = width * float(self.params.get("session_projection_fraction", 1.0))

        if episode.direction == "LONG":
            stop = min(observation.low, acceptance_low, episode.level) - buffer_value
            candidates = [
                (
                    self._previous_day_high
                    if self._previous_day_high is not None and self._previous_day_high > observation.close
                    else None,
                    "NEXT_PREVIOUS_DAY_HIGH",
                ),
                (episode.level + projection, "ACCEPTED_SESSION_RANGE_PROJECTION"),
            ]
        else:
            stop = max(observation.high, acceptance_high, episode.level) + buffer_value
            candidates = [
                (
                    self._previous_day_low
                    if self._previous_day_low is not None and self._previous_day_low < observation.close
                    else None,
                    "NEXT_PREVIOUS_DAY_LOW",
                ),
                (episode.level - projection, "ACCEPTED_SESSION_RANGE_PROJECTION"),
            ]
        target = self._select_target(episode.direction, observation.close, stop, candidates)
        if target is None:
            return self._reset(
                snapshot,
                episode,
                "NO_CONTINUATION_OBJECTIVE_AFTER_IMPULSE_ORIGIN_STOP",
            )
        return self._emit(
            snapshot,
            episode,
            stop,
            target[0],
            target[1],
            "SESSION_ACCEPTANCE_RETEST_HELD_WITH_IMPULSE_ORIGIN_INVALIDATION",
        )

    def _emit(self, snapshot, episode, stop, target, target_reason, reason):
        step = super()._emit(snapshot, episode, stop, target, target_reason, reason)
        self._acceptance_origins.pop(episode.scenario_id, None)
        return step

    def _reset(self, snapshot, episode, reason):
        step = super()._reset(snapshot, episode, reason)
        self._acceptance_origins.pop(episode.scenario_id, None)
        return step
