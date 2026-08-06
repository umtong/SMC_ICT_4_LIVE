"""Session sweep -> displacement -> first retracement engine for candidate-06 v0.4."""

from __future__ import annotations

from typing import Any

from lrb_types import PrimitiveSnapshot, ScenarioStep, ScenarioTransition
from session_engine import SessionLiquidityTransferEngine, _SessionEpisode


class SessionDisplacementRetestEngine(SessionLiquidityTransferEngine):
    """Require a post-sweep displacement and its first causal retracement.

    Reversal entries are not emitted from the response bar.  The response must
    reclaim the swept session boundary with opposite displacement.  The engine
    then waits for price to retrace a fixed fraction of the impulse and reject it
    while the original sweep extreme remains valid.  Continuation episodes keep
    the parent engine's acceptance-and-retest sequence.
    """

    def _advance(self, snapshot: PrimitiveSnapshot) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        if episode.family == "PENDING":
            return self._classify_response(snapshot, episode)
        if episode.family == "SRR_RETRACE":
            return self._advance_reversal_retrace(snapshot, episode)
        return super()._advance_retest(snapshot, episode)

    def _classify_response(self, snapshot: PrimitiveSnapshot, episode: _SessionEpisode) -> ScenarioStep:
        elapsed = snapshot.index - episode.started_index
        maximum = int(self.params.get("session_response_bars", 3))
        if elapsed > maximum:
            return self._reset(snapshot, episode, "SESSION_DISPLACEMENT_RESPONSE_EXPIRED")

        obs = snapshot.observation
        body_floor = float(self.params.get("session_displacement_body_atr", 0.45))
        close_distance = float(self.params.get("session_displacement_close_atr", 0.08)) * snapshot.atr
        flow_floor = float(self.params.get("session_displacement_flow_ratio", 0.05))
        use_flow = bool(self.params.get("session_use_flow_proxy", True))
        acceptance_distance = float(self.params.get("session_acceptance_close_atr", 0.12)) * snapshot.atr
        acceptance_body = float(self.params.get("session_acceptance_body_atr", 0.45))
        acceptance_flow = float(self.params.get("session_acceptance_flow_ratio", 0.08))

        if episode.side == "UPPER":
            reversal = (
                obs.close <= episode.level - close_distance
                and obs.close < obs.open
                and snapshot.body_atr >= body_floor
                and obs.close < episode.sweep_midpoint
                and ((snapshot.flow_ratio <= -flow_floor) if use_flow else snapshot.close_location <= 0.40)
            )
            continuation = (
                obs.close >= episode.level + acceptance_distance
                and obs.close > obs.open
                and snapshot.body_atr >= acceptance_body
                and ((snapshot.flow_ratio >= acceptance_flow) if use_flow else snapshot.close_location >= 0.65)
            )
            reversal_direction, continuation_direction = "SHORT", "LONG"
        else:
            reversal = (
                obs.close >= episode.level + close_distance
                and obs.close > obs.open
                and snapshot.body_atr >= body_floor
                and obs.close > episode.sweep_midpoint
                and ((snapshot.flow_ratio >= flow_floor) if use_flow else snapshot.close_location >= 0.60)
            )
            continuation = (
                obs.close <= episode.level - acceptance_distance
                and obs.close < obs.open
                and snapshot.body_atr >= acceptance_body
                and ((snapshot.flow_ratio <= -acceptance_flow) if use_flow else snapshot.close_location <= 0.35)
            )
            reversal_direction, continuation_direction = "LONG", "SHORT"

        enable_srr = bool(self.params.get("enable_srr", True))
        enable_sac = bool(self.params.get("enable_sac", True))
        reversal = reversal and enable_srr
        continuation = continuation and enable_sac
        if reversal and continuation:
            return self._reset(snapshot, episode, "SESSION_DISPLACEMENT_DIRECTION_CONFLICT")
        if reversal:
            previous = episode.state
            episode.family = "SRR_RETRACE"
            episode.direction = reversal_direction
            episode.state = f"{episode.side}_SESSION_DISPLACEMENT_RETRACE"
            episode.response_high = obs.high
            episode.response_low = obs.low
            episode.response_close = obs.close
            episode.response_index = snapshot.index
            transition = self._transition(
                episode,
                episode.state,
                "SESSION_SWEEP_RECLAIMED_WITH_MARKET_STRUCTURE_DISPLACEMENT",
                obs.close,
                {
                    "elapsed_bars": elapsed,
                    "body_atr": snapshot.body_atr,
                    "flow_ratio": snapshot.flow_ratio,
                    "sweep_midpoint": episode.sweep_midpoint,
                },
                previous_state=previous,
            )
            return ScenarioStep(transitions=(transition,))
        if continuation:
            previous = episode.state
            episode.family = "SAC"
            episode.direction = continuation_direction
            episode.state = f"{episode.side}_SESSION_SAC_RETEST"
            return ScenarioStep(
                transitions=(
                    self._transition(
                        episode,
                        episode.state,
                        "SESSION_BOUNDARY_ACCEPTED_WITH_DISPLACEMENT",
                        obs.close,
                        {"elapsed_bars": elapsed, "body_atr": snapshot.body_atr, "flow_ratio": snapshot.flow_ratio},
                        previous_state=previous,
                    ),
                ),
            )
        return ScenarioStep()

    def _advance_reversal_retrace(self, snapshot: PrimitiveSnapshot, episode: _SessionEpisode) -> ScenarioStep:
        obs = snapshot.observation
        response_index = int(getattr(episode, "response_index"))
        elapsed = snapshot.index - response_index
        if elapsed <= 0:
            return ScenarioStep()
        if elapsed > int(self.params.get("session_displacement_retest_bars", 6)):
            return self._reset(snapshot, episode, "SESSION_DISPLACEMENT_RETEST_EXPIRED")

        invalidation = float(self.params.get("session_displacement_invalidation_atr", 0.05)) * snapshot.atr
        fraction = float(self.params.get("session_displacement_retrace_fraction", 0.50))
        max_opposing_flow = float(self.params.get("session_displacement_max_opposing_flow", 0.22))
        rejection_body = float(self.params.get("session_retest_rejection_body_atr", 0.12))
        use_flow = bool(self.params.get("session_use_flow_proxy", True))

        if episode.direction == "SHORT":
            if obs.high > episode.extreme + invalidation or obs.close > episode.level + invalidation:
                return self._reset(snapshot, episode, "UPPER_SWEEP_EXTREME_INVALIDATED_BEFORE_RETRACE_ENTRY")
            trigger = float(getattr(episode, "response_low")) + fraction * (
                episode.extreme - float(getattr(episode, "response_low"))
            )
            target_candidate = self._reversal_target_candidate(episode, "SHORT")
            if target_candidate is not None and obs.low <= target_candidate[0]:
                return self._reset(snapshot, episode, "REVERSAL_TARGET_REACHED_BEFORE_FIRST_RETRACE")
            touched = obs.high >= trigger
            rejected = obs.close < obs.open and obs.close < trigger and snapshot.body_atr >= rejection_body
            flow_ok = snapshot.flow_ratio <= max_opposing_flow if use_flow else True
        else:
            if obs.low < episode.extreme - invalidation or obs.close < episode.level - invalidation:
                return self._reset(snapshot, episode, "LOWER_SWEEP_EXTREME_INVALIDATED_BEFORE_RETRACE_ENTRY")
            trigger = episode.extreme - fraction * (
                float(getattr(episode, "response_high")) - episode.extreme
            )
            target_candidate = self._reversal_target_candidate(episode, "LONG")
            if target_candidate is not None and obs.high >= target_candidate[0]:
                return self._reset(snapshot, episode, "REVERSAL_TARGET_REACHED_BEFORE_FIRST_RETRACE")
            touched = obs.low <= trigger
            rejected = obs.close > obs.open and obs.close > trigger and snapshot.body_atr >= rejection_body
            flow_ok = snapshot.flow_ratio >= -max_opposing_flow if use_flow else True

        if touched and rejected and flow_ok:
            episode.family = "SRR"
            return self._arm_reversal(snapshot, episode)
        return ScenarioStep()

    def _reversal_target_candidate(self, episode: _SessionEpisode, direction: str) -> tuple[float, str] | None:
        candidates: list[tuple[float | None, str]]
        if direction == "SHORT":
            candidates = [
                (episode.range_low, "OPPOSITE_SESSION_RANGE_LIQUIDITY"),
                (self._asia_low, "ASIA_LOW_LIQUIDITY"),
                (self._previous_day_low, "PREVIOUS_DAY_LOW_LIQUIDITY"),
            ]
            valid = [(float(price), reason) for price, reason in candidates if price is not None and price < episode.level]
            valid.sort(key=lambda value: episode.level - value[0])
        else:
            candidates = [
                (episode.range_high, "OPPOSITE_SESSION_RANGE_LIQUIDITY"),
                (self._asia_high, "ASIA_HIGH_LIQUIDITY"),
                (self._previous_day_high, "PREVIOUS_DAY_HIGH_LIQUIDITY"),
            ]
            valid = [(float(price), reason) for price, reason in candidates if price is not None and price > episode.level]
            valid.sort(key=lambda value: value[0] - episode.level)
        return valid[0] if valid else None
