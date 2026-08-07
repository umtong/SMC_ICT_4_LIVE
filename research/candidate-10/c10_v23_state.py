"""Open-interest semantic routing for candidate 10 v23.

v22 proved that completed eight-hour funding-session targets restore executable
reward distance, but it treated falling-OI clearing and rising-OI building as
the same acceptance-continuation state.  This module changes only that semantic
mapping:

* BUILDING + accepted break keeps v22 continuation logic.
* CLEARING + accepted break becomes a reclaim wait.  A reversal plan can exist
  only after a completed bar re-enters the old range and executed flow reverses.

All numeric thresholds, pool identities, expiry, entry timing, stop buffer,
external target hierarchy, cost model and risk contract remain unchanged.
"""
from __future__ import annotations

from dataclasses import replace

from c10_liquidation_state import AuctionProbe
from c10_liquidation_state import FiveMinuteAuctionBar
from c10_liquidation_state import LiquidationPlan
from c10_liquidation_state import LiquidationTransition
from c10_v22_install import ExternalTargetImpactStateMachine


class OISemanticExternalTargetStateMachine(ExternalTargetImpactStateMachine):
    """Route accepted breaks by whether leverage is clearing or building."""

    def _start_probe(
        self,
        bar: FiveMinuteAuctionBar,
        features: dict[str, float],
        oi_change: float,
    ) -> list[LiquidationTransition]:
        events = super()._start_probe(bar, features, oi_change)
        probe = self.active_probe
        if not (
            probe is not None
            and probe.mode == "ACCEPTANCE"
            and probe.oi_state == "CLEARING"
        ):
            return events

        self.counters["CLEARING_ACCEPTANCE_RECLAIM_WAIT_CREATED"] += 1
        routed: list[LiquidationTransition] = []
        for event in events:
            details = dict(event.details)
            details.update(
                {
                    "oi_semantic_mapping": "CLEARING_ACCEPTANCE_TO_RECLAIM_REVERSAL",
                    "continuation_entry_allowed": False,
                    "required_next_state": "RANGE_RECLAIM_AND_OPPOSITE_FLOW",
                },
            )
            routed.append(
                replace(
                    event,
                    next_state="CLEARING_RECLAIM_WAIT",
                    reason_code=(
                        "OI_CLEARING_ACCEPTED_BREAK_REQUIRES_RANGE_RECLAIM"
                    ),
                    details=details,
                ),
            )
        return routed

    def _build_clearing_reversal_plan(
        self,
        *,
        bar: FiveMinuteAuctionBar,
        probe: AuctionProbe,
        features: dict[str, float],
        direction: int,
    ) -> LiquidationPlan | None:
        # Reuse the already-tested rejection geometry without mutating the
        # active probe: stop beyond the actual raid extreme, external target in
        # the reversal direction, and the unchanged live impact/cost gate.
        rejection_geometry = replace(probe, mode="REJECTION")
        plan = super()._build_plan(
            bar=bar,
            probe=rejection_geometry,
            features=features,
            direction=direction,
        )
        if plan is None:
            return None
        details = dict(plan.details)
        details.update(
            {
                "mode": "CLEARING_ACCEPTANCE_RECLAIM",
                "original_probe_mode": "ACCEPTANCE",
                "oi_semantic_mapping": "CLEARING_EXHAUSTION_REVERSAL",
                "outward_direction": probe.outward_direction,
                "reversal_direction": direction,
                "reclaim_close": bar.close,
                "reclaim_executed_imbalance": bar.executed_imbalance,
            },
        )
        return replace(
            plan,
            scenario="LIQUIDATION_CLEARING_EXHAUSTION_REVERSAL",
            details=details,
        )

    def _process_probe(
        self,
        bar: FiveMinuteAuctionBar,
        features: dict[str, float],
    ) -> tuple[list[LiquidationTransition], LiquidationPlan | None]:
        probe = self.active_probe
        assert probe is not None
        if not (
            probe.mode == "ACCEPTANCE"
            and probe.oi_state == "CLEARING"
        ):
            # BUILDING acceptance and original sweep rejection are exact v22.
            return super()._process_probe(bar, features)

        age = self.sequence - probe.initiated_sequence
        if probe.source_side == "HIGH":
            probe.raid_extreme = max(probe.raid_extreme, bar.high)
        else:
            probe.raid_extreme = min(probe.raid_extreme, bar.low)

        direction = -probe.outward_direction
        inside_distance = self.params.confirmation_inside_atr * features["atr"]
        price_reclaimed = (
            bar.close <= probe.source_price - inside_distance
            if direction < 0
            else bar.close >= probe.source_price + inside_distance
        )
        flow_reversed = bar.executed_imbalance * direction > 0.0
        events: list[LiquidationTransition] = []

        if price_reclaimed and flow_reversed:
            plan = self._build_clearing_reversal_plan(
                bar=bar,
                probe=probe,
                features=features,
                direction=direction,
            )
            if plan is None:
                self.counters["CLEARING_RECLAIM_NO_EXECUTABLE_PLAN"] += 1
                events.append(
                    self._transition(
                        scenario_id=probe.scenario_id,
                        bar=bar,
                        event_type="SCENARIO_INVALIDATED",
                        previous_state="CLEARING_RECLAIM_WAIT",
                        next_state="INVALIDATED",
                        reason_code=(
                            "NO_COST_QUALIFIED_EXTERNAL_TARGET_AFTER_CLEARING_RECLAIM"
                        ),
                        reference_price=bar.close,
                        details={
                            "age_bars": age,
                            "direction": direction,
                            "price_reclaimed": price_reclaimed,
                            "flow_reversed": flow_reversed,
                            "confirmation_flow": bar.executed_imbalance,
                        },
                    ),
                )
                self._release_probe_pool(consumed=True)
                return events, None

            self.counters["CLEARING_ACCEPTANCE_RECLAIM_CONFIRMED"] += 1
            self.counters["TRADE_PLAN_CREATED"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="AUCTION_RESULT_CONFIRMED",
                    previous_state="CLEARING_RECLAIM_WAIT",
                    next_state="ENTRY_READY",
                    reason_code=(
                        "CLEARING_BREAK_RECLAIMED_WITH_OPPOSITE_EXECUTED_FLOW"
                    ),
                    reference_price=bar.close,
                    details={
                        "direction": direction,
                        "entry": plan.entry_estimate,
                        "stop": plan.stop_price,
                        "target": plan.target_price,
                        "target_pool_id": plan.target_pool_id,
                        "cost_adjusted_net_rr": plan.cost_adjusted_net_rr,
                        "confirmation_flow": bar.executed_imbalance,
                        "initial_flow": probe.initial_imbalance,
                        "initial_oi_change": probe.initial_oi_change,
                        "oi_state": probe.oi_state,
                        "age_bars": age,
                    },
                ),
            )
            self._release_probe_pool(consumed=True)
            return events, plan

        if age >= self.params.probe_max_bars:
            self.counters["CLEARING_ACCEPTANCE_RECLAIM_EXPIRED"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="SCENARIO_EXPIRED",
                    previous_state="CLEARING_RECLAIM_WAIT",
                    next_state="EXPIRED",
                    reason_code=(
                        "NO_RECLAIM_WITH_OPPOSITE_FLOW_AFTER_CLEARING_BREAK"
                    ),
                    reference_price=bar.close,
                    details={
                        "age_bars": age,
                        "price_reclaimed": price_reclaimed,
                        "flow_reversed": flow_reversed,
                        "confirmation_flow": bar.executed_imbalance,
                    },
                ),
            )
            self._release_probe_pool(consumed=True)
        return events, None


__all__ = ["OISemanticExternalTargetStateMachine"]
