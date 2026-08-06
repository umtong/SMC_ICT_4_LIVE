"""Episode confirmation and retest progression for candidate-06."""

from __future__ import annotations

from lrb_types import PrimitiveSnapshot, ScenarioStep


class ScenarioConfirmMixin:
    """Advance active SRR and SAC episodes through confirmation or retest."""

    def _advance_episode(self, snapshot: PrimitiveSnapshot) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        elapsed = snapshot.index - episode.started_index
        obs = snapshot.observation
        previous_extreme = episode.extreme

        if episode.family == "SRR":
            if elapsed > int(self.params["rejection_confirm_bars"]):
                return self._reset(snapshot, "REJECTION_CONFIRMATION_EXPIRED")
            if episode.side == "UPPER":
                if obs.close > previous_extreme + float(self.params["invalidation_buffer_atr"]) * snapshot.atr:
                    return self._reset(snapshot, "UPPER_REJECTION_INVALIDATED_BY_ACCEPTANCE")
                episode.extreme = max(previous_extreme, obs.high)
                bearish_progress = obs.close < obs.open and snapshot.body_atr >= float(self.params["confirm_body_atr"])
                flow_flip = snapshot.flow_ratio <= -float(self.params["confirm_flow_ratio"])
                price_failure = obs.close <= episode.midpoint - float(self.params["confirm_midpoint_atr"]) * snapshot.atr
                if obs.close < episode.level and bearish_progress and (flow_flip or price_failure):
                    return self._arm_reversal(snapshot, episode)
            else:
                if obs.close < previous_extreme - float(self.params["invalidation_buffer_atr"]) * snapshot.atr:
                    return self._reset(snapshot, "LOWER_REJECTION_INVALIDATED_BY_ACCEPTANCE")
                episode.extreme = min(previous_extreme, obs.low)
                bullish_progress = obs.close > obs.open and snapshot.body_atr >= float(self.params["confirm_body_atr"])
                flow_flip = snapshot.flow_ratio >= float(self.params["confirm_flow_ratio"])
                price_failure = obs.close >= episode.midpoint + float(self.params["confirm_midpoint_atr"]) * snapshot.atr
                if obs.close > episode.level and bullish_progress and (flow_flip or price_failure):
                    return self._arm_reversal(snapshot, episode)
            return ScenarioStep()

        episode.extreme = max(previous_extreme, obs.high) if episode.side == "UPPER" else min(previous_extreme, obs.low)
        if elapsed > int(self.params["acceptance_retest_bars"]):
            return self._reset(snapshot, "ACCEPTANCE_RETEST_EXPIRED")
        tolerance = float(self.params["acceptance_reclaim_tolerance_atr"]) * snapshot.atr
        band = float(self.params["retest_band_atr"]) * snapshot.atr
        if episode.direction == "LONG":
            if obs.close < episode.level - tolerance:
                return self._reset(snapshot, "UPPER_ACCEPTANCE_RECLAIMED_INSIDE_RANGE")
            target_candidate = self._continuation_target(snapshot, episode)
            if target_candidate is not None and obs.high >= target_candidate[0]:
                return self._reset(snapshot, "TARGET_REACHED_BEFORE_RETEST")
            retested = obs.low <= episode.level + band and obs.close > episode.level
            response_ok = snapshot.flow_ratio >= -float(self.params["retest_max_opposing_flow"])
            if retested and response_ok and snapshot.close_location >= float(self.params["retest_close_location_floor"]):
                return self._arm_continuation(snapshot, episode)
        else:
            if obs.close > episode.level + tolerance:
                return self._reset(snapshot, "LOWER_ACCEPTANCE_RECLAIMED_INSIDE_RANGE")
            target_candidate = self._continuation_target(snapshot, episode)
            if target_candidate is not None and obs.low <= target_candidate[0]:
                return self._reset(snapshot, "TARGET_REACHED_BEFORE_RETEST")
            retested = obs.high >= episode.level - band and obs.close < episode.level
            response_ok = snapshot.flow_ratio <= float(self.params["retest_max_opposing_flow"])
            if retested and response_ok and snapshot.close_location <= 1.0 - float(self.params["retest_close_location_floor"]):
                return self._arm_continuation(snapshot, episode)
        return ScenarioStep()

