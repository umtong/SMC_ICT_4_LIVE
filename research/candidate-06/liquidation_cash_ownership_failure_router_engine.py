"""Route a failed liquidation-to-cash ownership relay into a reversal.

This engine preserves the full LCOR continuation state machine.  It adds one
categorical branch only after both cash and perpetual markets have accepted the
post-liquidation boundary: if cash then loses that boundary with adverse cash
flow and the perpetual closes back through its own boundary with matching
opposite initiative, the attempted ownership transfer is classified as a
failed auction rather than merely reset.

The failure bar is completed before the signal exists.  Its extreme defines the
new reversal leg's invalidation; targets must remain beyond that completed bar.
Orders, fills, fees, slippage, positions and NAV remain in NautilusTrader.
"""

from __future__ import annotations

from typing import Any, Mapping

from futures_metrics_data import FuturesMetric
from liquidation_cash_ownership_relay_engine import (
    LiquidationCashOwnershipRelayEngine,
)
from causal_inventory_ownership_transfer_engine import _OwnershipEpisode
from lrb_types import (
    BarObservation,
    PrimitiveSnapshot,
    ScenarioSignal,
    ScenarioStep,
    ScenarioTransition,
)


class LiquidationCashOwnershipFailureRouterEngine(
    LiquidationCashOwnershipRelayEngine,
):
    """Trade only a cross-venue failure of an already accepted cash relay."""

    FAILURE_FAMILY = "LCOR_F"
    FAILURE_INVALIDATION = "LCOR_FAILED_CASH_OWNERSHIP_REVERSAL_INVALIDATED"
    FAILURE_SIGNALLED = "LCOR_FAILURE_REVERSAL_SIGNALLED"

    def _failure_confirmation(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        spot_atr: float,
    ) -> tuple[str, dict[str, Any]] | None:
        if (
            episode.spot_owner_ts_ns is None
            or not episode.auction_confirmed
            or episode.auction_confirmation_index is None
            or snapshot.index <= episode.auction_confirmation_index
            or self._cash_holds(episode, spot, spot_atr)
        ):
            return None

        observation = snapshot.observation
        spot_flow_floor = float(
            self.params.get(
                "lcor_failure_spot_flow_ratio",
                self.params.get("lcor_response_flow_ratio", 0.05),
            ),
        )
        perp_flow_floor = float(
            self.params.get(
                "lcor_failure_perp_flow_ratio",
                self.params.get("lcor_response_flow_ratio", 0.05),
            ),
        )
        close_location = float(
            self.params.get(
                "lcor_failure_close_location",
                self.params.get("lcor_response_close_location", 0.58),
            ),
        )
        require_spot_flow = bool(
            self.params.get("lcor_failure_require_spot_flow", True),
        )
        require_perp_flow = bool(
            self.params.get("lcor_failure_require_perp_flow", True),
        )
        require_body = bool(
            self.params.get("lcor_failure_require_directional_body", True),
        )

        if episode.direction == "LONG":
            spot_flow_passed = spot.flow_ratio <= -spot_flow_floor
            perp_flow_passed = snapshot.flow_ratio <= -perp_flow_floor
            body_passed = observation.close < observation.open
            perpetual_failed = (
                observation.close < episode.perp_boundary
                and snapshot.close_location <= 1.0 - close_location
            )
            direction = "SHORT"
        elif episode.direction == "SHORT":
            spot_flow_passed = spot.flow_ratio >= spot_flow_floor
            perp_flow_passed = snapshot.flow_ratio >= perp_flow_floor
            body_passed = observation.close > observation.open
            perpetual_failed = (
                observation.close > episode.perp_boundary
                and snapshot.close_location >= close_location
            )
            direction = "LONG"
        else:
            return None

        if not perpetual_failed:
            return None
        if require_body and not body_passed:
            return None
        if require_spot_flow and not spot_flow_passed:
            return None
        if require_perp_flow and not perp_flow_passed:
            return None
        return direction, {
            "original_direction": episode.direction,
            "reversal_direction": direction,
            "spot_boundary": episode.spot_boundary,
            "perp_boundary": episode.perp_boundary,
            "spot_close": spot.close,
            "spot_flow_ratio": spot.flow_ratio,
            "perp_close": observation.close,
            "perp_flow_ratio": snapshot.flow_ratio,
            "perp_close_location": snapshot.close_location,
            "spot_flow_passed": spot_flow_passed,
            "perp_flow_passed": perp_flow_passed,
            "directional_body_passed": body_passed,
            "require_spot_flow": require_spot_flow,
            "require_perp_flow": require_perp_flow,
            "require_directional_body": require_body,
        }

    def _failure_target(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        direction: str,
        entry: float,
        stop: float,
    ) -> tuple[float, str] | None:
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        observation = snapshot.observation
        projection = episode.event_range * float(
            self.params.get("lcor_projection_fraction", 1.0),
        )
        if direction == "LONG":
            raw = [
                (episode.event_open, "FAILED_CASH_RELAY_IMPULSE_ORIGIN"),
                (episode.prior_perp_high, "FAILED_CASH_RELAY_PRIOR_AUCTION_LIQUIDITY"),
                (snapshot.upper_fast, "FAILED_CASH_RELAY_FAST_BUYSIDE_LIQUIDITY"),
                (snapshot.upper_slow, "FAILED_CASH_RELAY_SLOW_BUYSIDE_LIQUIDITY"),
                (entry + projection, "FAILED_CASH_RELAY_RANGE_PROJECTION"),
            ]
            candidates = sorted(
                (float(price), reason)
                for price, reason in raw
                if price is not None and float(price) > max(entry, observation.high)
            )
        else:
            raw = [
                (episode.event_open, "FAILED_CASH_RELAY_IMPULSE_ORIGIN"),
                (episode.prior_perp_low, "FAILED_CASH_RELAY_PRIOR_AUCTION_LIQUIDITY"),
                (snapshot.lower_fast, "FAILED_CASH_RELAY_FAST_SELLSIDE_LIQUIDITY"),
                (snapshot.lower_slow, "FAILED_CASH_RELAY_SLOW_SELLSIDE_LIQUIDITY"),
                (entry - projection, "FAILED_CASH_RELAY_RANGE_PROJECTION"),
            ]
            candidates = sorted(
                (
                    (float(price), reason)
                    for price, reason in raw
                    if price is not None and float(price) < min(entry, observation.low)
                ),
                reverse=True,
            )

        minimum_rr = float(self.params.get("minimum_structural_rr", 0.75))
        for price, reason in candidates:
            reward = price - entry if direction == "LONG" else entry - price
            if reward > 0.0 and reward / risk >= minimum_rr:
                return price, reason
        return None

    def _emit_failure_reversal(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        direction: str,
        confirmation: Mapping[str, Any],
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        if not allow_new:
            return self._reset(
                snapshot,
                "ENTRY_SLOT_UNAVAILABLE_AT_CASH_OWNERSHIP_FAILURE",
                confirmation,
            )
        observation = snapshot.observation
        buffer_value = float(
            self.params.get("lcor_stop_buffer_atr", 0.08),
        ) * episode.atr
        if direction == "LONG":
            stop = min(observation.low, episode.perp_boundary) - buffer_value
        else:
            stop = max(observation.high, episode.perp_boundary) + buffer_value
        target = self._failure_target(
            episode,
            snapshot,
            direction,
            observation.close,
            stop,
        )
        if target is None:
            return self._reset(
                snapshot,
                "NO_STILL_LIVE_FAILED_CASH_RELAY_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                {
                    **dict(confirmation),
                    "entry": observation.close,
                    "stop": stop,
                    "failure_bar_high": observation.high,
                    "failure_bar_low": observation.low,
                },
            )
        target_price, target_reason = target
        previous = episode.state
        episode.state = self.FAILURE_SIGNALLED
        episode.signal_index = snapshot.index
        context = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="LCOR_FAILURE_CONTEXT_TRANSITION",
            previous_state=previous,
            next_state=self.FAILURE_SIGNALLED,
            reason_code="CASH_AND_PERPETUAL_ACCEPTANCE_FAILED_WITH_OPPOSITE_INITIATIVE",
            reference_price=observation.close,
            details={
                **dict(confirmation),
                "stop": stop,
                "target": target_price,
                "target_reason": target_reason,
            },
        )
        entry_scenario_id = f"{episode.scenario_id}:FAILURE_ENTRY"
        entry = ScenarioTransition(
            scenario_id=entry_scenario_id,
            event_type="LCOR_FAILURE_ENTRY_TRANSITION",
            previous_state="IDLE",
            next_state="ENTRY_ARMED",
            reason_code="LCOR_FAILURE_REVERSAL_ENTRY_ARMED",
            reference_price=observation.close,
            details={
                "context_scenario_id": episode.scenario_id,
                "direction": direction,
                "stop": stop,
                "target": target_price,
                "target_reason": target_reason,
            },
        )
        signal = ScenarioSignal(
            scenario_id=entry_scenario_id,
            family=self.FAILURE_FAMILY,
            direction=direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=observation.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=episode.atr,
            liquidity_level=episode.perp_boundary,
            details={
                **dict(confirmation),
                "context_scenario_id": episode.scenario_id,
                "ownership_branch": self.BRANCH,
                "initiating_side": episode.side,
                "oi_change_fraction": episode.oi_change,
                "prior_only_oi_threshold": episode.oi_threshold,
                "prior_auction_end_ts_ns": episode.prior_auction_end_ts_ns,
                "spot_owner_ts_ns": episode.spot_owner_ts_ns,
                "perp_owner_ts_ns": episode.perp_owner_ts_ns,
                "causal_exit_reason_codes": (self.FAILURE_INVALIDATION,),
                "causal_exit_open_position": True,
            },
        )
        return ScenarioStep(transitions=(context, entry), signal=signal)

    def _advance_failure_signalled(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        spot_atr: float,
    ) -> ScenarioStep:
        assert episode.signal_index is not None
        observation = snapshot.observation
        distance = float(
            self.params.get("lcor_accept_close_atr", 0.05),
        ) * spot_atr
        perp_distance = float(
            self.params.get("lcor_perp_accept_close_atr", 0.04),
        ) * episode.atr
        flow = float(self.params.get("lcor_response_flow_ratio", 0.05))
        if episode.direction == "LONG":
            original_reaccepted = (
                spot.close >= episode.spot_boundary + distance
                and spot.flow_ratio >= flow
                and observation.close >= episode.perp_boundary + perp_distance
                and snapshot.flow_ratio >= flow
            )
        else:
            original_reaccepted = (
                spot.close <= episode.spot_boundary - distance
                and spot.flow_ratio <= -flow
                and observation.close <= episode.perp_boundary - perp_distance
                and snapshot.flow_ratio <= -flow
            )
        if original_reaccepted:
            return self._reset(
                snapshot,
                self.FAILURE_INVALIDATION,
                {
                    "original_direction": episode.direction,
                    "spot_close": spot.close,
                    "spot_flow_ratio": spot.flow_ratio,
                    "perp_close": observation.close,
                    "perp_flow_ratio": snapshot.flow_ratio,
                },
            )
        if snapshot.index - episode.signal_index >= int(
            self.params.get("lcor_post_signal_context_bars", 8),
        ):
            return self._reset(
                snapshot,
                "LCOR_FAILURE_REVERSAL_POST_SIGNAL_CONTEXT_MATURED",
            )
        return ScenarioStep()

    def _advance_episode(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric | None,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        if episode.branch != self.BRANCH:
            return super()._advance_episode(
                snapshot,
                spot,
                metric,
                allow_new=allow_new,
            )
        spot_atr = self._spot_atr()
        if spot_atr is None:
            return ScenarioStep()
        if episode.state == self.FAILURE_SIGNALLED:
            return self._advance_failure_signalled(
                episode,
                snapshot,
                spot,
                spot_atr,
            )
        if (
            bool(self.params.get("lcor_enable_failure_reversal", False))
            and snapshot.index > episode.started_index
        ):
            confirmation = self._failure_confirmation(
                episode,
                snapshot,
                spot,
                spot_atr,
            )
            if confirmation is not None:
                direction, details = confirmation
                return self._emit_failure_reversal(
                    episode,
                    snapshot,
                    direction,
                    details,
                    allow_new=allow_new,
                )
        return super()._advance_episode(
            snapshot,
            spot,
            metric,
            allow_new=allow_new,
        )
