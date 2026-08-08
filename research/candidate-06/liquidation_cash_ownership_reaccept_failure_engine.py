"""Route a false first LCOR failure through reacceptance into a second failure.

The parent LCOR failure router treats the first completed cross-venue ownership
failure as a reversal signal.  This variant models a different causal episode:

1. accepted cash/perpetual ownership fails with opposite initiative;
2. the original direction later reaccepts both boundaries with matching flow;
3. that recovery attempt fails again on a strictly later completed bar;
4. only the second failure opens a new reversal leg.

The recovery-test extreme, not the first failure extreme, defines invalidation.
Targets remain live opposite-side objectives and all execution/accounting stays
inside NautilusTrader.
"""

from __future__ import annotations

from typing import Any, Mapping

from causal_inventory_ownership_transfer_engine import _OwnershipEpisode
from futures_metrics_data import FuturesMetric
from liquidation_cash_ownership_failure_router_engine import (
    LiquidationCashOwnershipFailureRouterEngine,
)
from liquidation_cash_ownership_relay_engine import (
    LiquidationCashOwnershipRelayEngine,
)
from lrb_types import (
    BarObservation,
    PrimitiveSnapshot,
    ScenarioSignal,
    ScenarioStep,
    ScenarioTransition,
)


class LiquidationCashOwnershipReacceptFailureEngine(
    LiquidationCashOwnershipFailureRouterEngine,
):
    """Trade only a second ownership failure after an intervening reacceptance."""

    REACCEPT_FAILURE_FAMILY = "LCOR_RF"
    FIRST_FAILURE_WAIT = "LCOR_FIRST_FAILURE_AWAITING_ORIGINAL_REACCEPT"
    REACCEPT_TEST = "LCOR_ORIGINAL_DIRECTION_REACCEPT_TEST"
    REACCEPT_FAILURE_SIGNALLED = "LCOR_REACCEPT_FAILURE_REVERSAL_SIGNALLED"
    REACCEPT_FAILURE_INVALIDATION = (
        "LCOR_REACCEPT_FAILURE_REVERSAL_INVALIDATED"
    )

    def __init__(
        self,
        params: Mapping[str, Any],
        *,
        spot_observations: Mapping[int, BarObservation],
        metrics: Mapping[int, FuturesMetric],
    ) -> None:
        super().__init__(
            params,
            spot_observations=spot_observations,
            metrics=metrics,
        )
        self._reaccept_context: dict[str, dict[str, Any]] = {}

    def _reset(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioStep:
        scenario_id = (
            self._episode.scenario_id if self._episode is not None else None
        )
        step = super()._reset(snapshot, reason, details)
        if scenario_id is not None:
            self._reaccept_context.pop(scenario_id, None)
        return step

    def _original_direction_reaccepted(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        spot_atr: float,
    ) -> tuple[bool, dict[str, Any]]:
        observation = snapshot.observation
        spot_distance = float(
            self.params.get("lcor_accept_close_atr", 0.05),
        ) * spot_atr
        perp_distance = float(
            self.params.get("lcor_perp_accept_close_atr", 0.04),
        ) * episode.atr
        flow = float(self.params.get("lcor_response_flow_ratio", 0.05))
        if episode.direction == "LONG":
            passed = (
                spot.close >= episode.spot_boundary + spot_distance
                and spot.flow_ratio >= flow
                and observation.close >= episode.perp_boundary + perp_distance
                and snapshot.flow_ratio >= flow
            )
        elif episode.direction == "SHORT":
            passed = (
                spot.close <= episode.spot_boundary - spot_distance
                and spot.flow_ratio <= -flow
                and observation.close <= episode.perp_boundary - perp_distance
                and snapshot.flow_ratio <= -flow
            )
        else:
            passed = False
        return passed, {
            "original_direction": episode.direction,
            "spot_boundary": episode.spot_boundary,
            "perp_boundary": episode.perp_boundary,
            "spot_close": spot.close,
            "spot_flow_ratio": spot.flow_ratio,
            "perp_close": observation.close,
            "perp_flow_ratio": snapshot.flow_ratio,
            "spot_reaccept_distance": spot_distance,
            "perp_reaccept_distance": perp_distance,
        }

    def _record_first_failure(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        direction: str,
        confirmation: Mapping[str, Any],
    ) -> ScenarioStep:
        observation = snapshot.observation
        previous = episode.state
        episode.state = self.FIRST_FAILURE_WAIT
        self._reaccept_context[episode.scenario_id] = {
            "first_failure_direction": direction,
            "first_failure_index": snapshot.index,
            "first_failure_ts_ns": observation.ts_ns,
            "first_failure_open": observation.open,
            "first_failure_high": observation.high,
            "first_failure_low": observation.low,
            "first_failure_close": observation.close,
            "first_failure_confirmation": dict(confirmation),
        }
        transition = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="LCOR_REACCEPT_FAILURE_CONTEXT_TRANSITION",
            previous_state=previous,
            next_state=self.FIRST_FAILURE_WAIT,
            reason_code=(
                "FIRST_CROSS_VENUE_OWNERSHIP_FAILURE_OBSERVED_"
                "AWAITING_ORIGINAL_REACCEPT"
            ),
            reference_price=observation.close,
            details={
                **dict(confirmation),
                "first_failure_direction": direction,
                "first_failure_index": snapshot.index,
                "first_failure_ts_ns": observation.ts_ns,
            },
        )
        return ScenarioStep(transitions=(transition,))

    def _retained_inventory_guard(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric | None,
    ) -> ScenarioStep | None:
        inventory, lost, detail = self._contraction_update(episode, metric)
        if lost:
            return self._reset(
                snapshot,
                "FORCED_LIQUIDATION_REMOVAL_NO_LONGER_RETAINED_"
                "DURING_REACCEPT_FAILURE_SEQUENCE",
                detail,
            )
        if inventory:
            episode.inventory_confirmed = True
        return None

    def _advance_first_failure_wait(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric | None,
        spot_atr: float,
    ) -> ScenarioStep:
        context = self._reaccept_context.get(episode.scenario_id)
        if context is None:
            return self._reset(
                snapshot,
                "LCOR_REACCEPT_FAILURE_CONTEXT_MISSING",
            )
        observation = snapshot.observation
        episode.high_since_event = max(
            episode.high_since_event,
            observation.high,
        )
        episode.low_since_event = min(
            episode.low_since_event,
            observation.low,
        )
        guarded = self._retained_inventory_guard(
            episode,
            snapshot,
            metric,
        )
        if guarded is not None:
            return guarded

        reaccepted, details = self._original_direction_reaccepted(
            episode,
            snapshot,
            spot,
            spot_atr,
        )
        if reaccepted:
            previous = episode.state
            episode.state = self.REACCEPT_TEST
            context.update(
                {
                    "reaccept_index": snapshot.index,
                    "reaccept_ts_ns": observation.ts_ns,
                    "reaccept_open": observation.open,
                    "reaccept_high": observation.high,
                    "reaccept_low": observation.low,
                    "reaccept_close": observation.close,
                    "reaccept_spot_high": spot.high,
                    "reaccept_spot_low": spot.low,
                    "reaccept_details": details,
                },
            )
            transition = ScenarioTransition(
                scenario_id=episode.scenario_id,
                event_type="LCOR_REACCEPT_FAILURE_CONTEXT_TRANSITION",
                previous_state=previous,
                next_state=self.REACCEPT_TEST,
                reason_code=(
                    "ORIGINAL_DIRECTION_REACCEPTED_BOTH_BOUNDARIES_"
                    "AFTER_FIRST_FAILURE"
                ),
                reference_price=observation.close,
                details={
                    **details,
                    "first_failure_ts_ns": context["first_failure_ts_ns"],
                    "reaccept_index": snapshot.index,
                    "reaccept_ts_ns": observation.ts_ns,
                    "reaccept_high": observation.high,
                    "reaccept_low": observation.low,
                },
            )
            return ScenarioStep(transitions=(transition,))

        if snapshot.index - int(context["first_failure_index"]) >= int(
            self.params.get("lcor_post_signal_context_bars", 8),
        ):
            return self._reset(
                snapshot,
                "FIRST_OWNERSHIP_FAILURE_NOT_REACCEPTED_WITHIN_CONTEXT",
            )
        return ScenarioStep()

    def _emit_reaccept_failure(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        direction: str,
        confirmation: Mapping[str, Any],
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        context = self._reaccept_context.get(episode.scenario_id)
        if context is None:
            return self._reset(
                snapshot,
                "LCOR_REACCEPT_FAILURE_CONTEXT_MISSING_AT_ENTRY",
            )
        if not allow_new:
            return self._reset(
                snapshot,
                "ENTRY_SLOT_UNAVAILABLE_AT_REACCEPT_FAILURE",
                confirmation,
            )

        observation = snapshot.observation
        buffer_value = float(
            self.params.get("lcor_stop_buffer_atr", 0.08),
        ) * episode.atr
        if direction == "LONG":
            stop = (
                min(
                    float(context["reaccept_low"]),
                    observation.low,
                    episode.perp_boundary,
                )
                - buffer_value
            )
        else:
            stop = (
                max(
                    float(context["reaccept_high"]),
                    observation.high,
                    episode.perp_boundary,
                )
                + buffer_value
            )

        target = self._failure_target(
            episode,
            snapshot,
            direction,
            observation.close,
            stop,
        )
        combined = {
            **dict(confirmation),
            "first_failure_direction": context["first_failure_direction"],
            "first_failure_index": context["first_failure_index"],
            "first_failure_ts_ns": context["first_failure_ts_ns"],
            "first_failure_high": context["first_failure_high"],
            "first_failure_low": context["first_failure_low"],
            "reaccept_index": context["reaccept_index"],
            "reaccept_ts_ns": context["reaccept_ts_ns"],
            "reaccept_high": context["reaccept_high"],
            "reaccept_low": context["reaccept_low"],
            "second_failure_index": snapshot.index,
            "second_failure_ts_ns": observation.ts_ns,
            "second_failure_high": observation.high,
            "second_failure_low": observation.low,
        }
        if target is None:
            return self._reset(
                snapshot,
                "NO_STILL_LIVE_REACCEPT_FAILURE_OBJECTIVE_WITH_"
                "SUFFICIENT_SPACE",
                {
                    **combined,
                    "entry": observation.close,
                    "stop": stop,
                },
            )

        target_price, target_reason = target
        previous = episode.state
        episode.state = self.REACCEPT_FAILURE_SIGNALLED
        episode.signal_index = snapshot.index
        context_transition = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="LCOR_REACCEPT_FAILURE_CONTEXT_TRANSITION",
            previous_state=previous,
            next_state=self.REACCEPT_FAILURE_SIGNALLED,
            reason_code=(
                "REACCEPTED_OWNERSHIP_FAILED_AGAIN_WITH_OPPOSITE_INITIATIVE"
            ),
            reference_price=observation.close,
            details={
                **combined,
                "stop": stop,
                "target": target_price,
                "target_reason": target_reason,
            },
        )
        entry_scenario_id = (
            f"{episode.scenario_id}:REACCEPT_FAILURE_ENTRY"
        )
        entry_transition = ScenarioTransition(
            scenario_id=entry_scenario_id,
            event_type="LCOR_REACCEPT_FAILURE_ENTRY_TRANSITION",
            previous_state="IDLE",
            next_state="ENTRY_ARMED",
            reason_code="LCOR_REACCEPT_FAILURE_ENTRY_ARMED",
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
            family=self.REACCEPT_FAILURE_FAMILY,
            direction=direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=observation.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=episode.atr,
            liquidity_level=episode.perp_boundary,
            details={
                **combined,
                "context_scenario_id": episode.scenario_id,
                "ownership_branch": self.BRANCH,
                "initiating_side": episode.side,
                "oi_change_fraction": episode.oi_change,
                "prior_only_oi_threshold": episode.oi_threshold,
                "prior_auction_end_ts_ns": episode.prior_auction_end_ts_ns,
                "spot_owner_ts_ns": episode.spot_owner_ts_ns,
                "perp_owner_ts_ns": episode.perp_owner_ts_ns,
                "causal_exit_reason_codes": (
                    self.REACCEPT_FAILURE_INVALIDATION,
                ),
                "causal_exit_open_position": True,
            },
        )
        return ScenarioStep(
            transitions=(context_transition, entry_transition),
            signal=signal,
        )

    def _advance_reaccept_test(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric | None,
        spot_atr: float,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        context = self._reaccept_context.get(episode.scenario_id)
        if context is None:
            return self._reset(
                snapshot,
                "LCOR_REACCEPT_FAILURE_CONTEXT_MISSING",
            )
        observation = snapshot.observation
        episode.high_since_event = max(
            episode.high_since_event,
            observation.high,
        )
        episode.low_since_event = min(
            episode.low_since_event,
            observation.low,
        )
        guarded = self._retained_inventory_guard(
            episode,
            snapshot,
            metric,
        )
        if guarded is not None:
            return guarded
        if snapshot.index <= int(context["reaccept_index"]):
            return ScenarioStep()

        still_reaccepted, _ = self._original_direction_reaccepted(
            episode,
            snapshot,
            spot,
            spot_atr,
        )
        if still_reaccepted:
            context["reaccept_high"] = max(
                float(context["reaccept_high"]),
                observation.high,
            )
            context["reaccept_low"] = min(
                float(context["reaccept_low"]),
                observation.low,
            )

        confirmation = self._failure_confirmation(
            episode,
            snapshot,
            spot,
            spot_atr,
        )
        if confirmation is not None:
            direction, details = confirmation
            if direction == context["first_failure_direction"]:
                return self._emit_reaccept_failure(
                    episode,
                    snapshot,
                    direction,
                    details,
                    allow_new=allow_new,
                )

        if snapshot.index - int(context["reaccept_index"]) >= int(
            self.params.get("lcor_post_signal_context_bars", 8),
        ):
            return self._reset(
                snapshot,
                "ORIGINAL_DIRECTION_REACCEPT_HELD_WITHOUT_SECOND_FAILURE",
            )
        return ScenarioStep()

    def _advance_reaccept_failure_signalled(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric | None,
        spot_atr: float,
    ) -> ScenarioStep:
        assert episode.signal_index is not None
        guarded = self._retained_inventory_guard(
            episode,
            snapshot,
            metric,
        )
        if guarded is not None:
            return guarded
        reaccepted, details = self._original_direction_reaccepted(
            episode,
            snapshot,
            spot,
            spot_atr,
        )
        if reaccepted:
            return self._reset(
                snapshot,
                self.REACCEPT_FAILURE_INVALIDATION,
                details,
            )
        if snapshot.index - episode.signal_index >= int(
            self.params.get("lcor_post_signal_context_bars", 8),
        ):
            return self._reset(
                snapshot,
                "LCOR_REACCEPT_FAILURE_POST_SIGNAL_CONTEXT_MATURED",
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
            return LiquidationCashOwnershipRelayEngine._advance_episode(
                self,
                snapshot,
                spot,
                metric,
                allow_new=allow_new,
            )
        spot_atr = self._spot_atr()
        if spot_atr is None:
            return ScenarioStep()
        if episode.state == self.FIRST_FAILURE_WAIT:
            return self._advance_first_failure_wait(
                episode,
                snapshot,
                spot,
                metric,
                spot_atr,
            )
        if episode.state == self.REACCEPT_TEST:
            return self._advance_reaccept_test(
                episode,
                snapshot,
                spot,
                metric,
                spot_atr,
                allow_new=allow_new,
            )
        if episode.state == self.REACCEPT_FAILURE_SIGNALLED:
            return self._advance_reaccept_failure_signalled(
                episode,
                snapshot,
                spot,
                metric,
                spot_atr,
            )

        if (
            bool(
                self.params.get(
                    "lcor_enable_reaccept_failure_reversal",
                    False,
                ),
            )
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
                return self._record_first_failure(
                    episode,
                    snapshot,
                    direction,
                    details,
                )

        return LiquidationCashOwnershipRelayEngine._advance_episode(
            self,
            snapshot,
            spot,
            metric,
            allow_new=allow_new,
        )
