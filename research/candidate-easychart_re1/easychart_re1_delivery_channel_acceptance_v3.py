"""Channel acceptance system with non-chasing continuation routing.

A five-minute rebalance continuation is an early pullback within an established
external delivery, not a late entry after most of the source-to-draw auction has
already completed.  The natural balance point is the midpoint between acquired
external liquidity and the matching-scale draw.

Continuation plans are therefore executable only while entry remains in the
source-side half of that delivery and their immutable target does not project
through the external draw.  Channel S/R flips are not subjected to this rule:
a newly accepted channel edge is a distinct structural event and may occur in
the second half of delivery.
"""
from __future__ import annotations

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Side
from easychart_re1_delivery_channel_acceptance_v2 import (
    EasyChartRE1DeliveryChannelAcceptanceV2Bundle,
)


PROXIMAL_DELIVERY_CONTINUATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "REBALANCE_CONTINUATION_ENTRY_MUST_REMAIN_ON_THE_SOURCE_SIDE_OF_THE_EXTERNAL_DELIVERY_MIDPOINT"
)
EXTERNAL_DRAW_TARGET_CAP_RULE = (
    "SOURCE_EXPLICIT:"
    "A_LOCAL_CONTINUATION_OBJECTIVE_MAY_NOT_PROJECT_THROUGH_THE_ACTIVE_MATCHING_SCALE_EXTERNAL_LIQUIDITY_DRAW"
)
for _rule in (
    PROXIMAL_DELIVERY_CONTINUATION_RULE,
    EXTERNAL_DRAW_TARGET_CAP_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class EasyChartRE1DeliveryChannelAcceptanceV3Bundle(
    EasyChartRE1DeliveryChannelAcceptanceV2Bundle,
):
    def _route_continuation(
        self,
        raw: list[V5TradePlan],
    ) -> list[V5TradePlan]:
        active = self.delivery_draw.active
        if active is None:
            self._dinc("continuation_without_active_draw")
            return []
        midpoint = (active.source_pivot_price + active.target_price) / 2.0
        eligible: list[V5TradePlan] = []
        for plan in raw:
            proximal = (
                plan.entry <= midpoint
                if plan.side is Side.LONG
                else plan.entry >= midpoint
            )
            target_within_draw = (
                plan.target <= active.target_price
                if plan.side is Side.LONG
                else plan.target >= active.target_price
            )
            if not proximal:
                self._dinc("continuation_deferred_past_delivery_midpoint")
                self._delivery_trace.append(
                    {
                        "scenario_kind": "continuation_deferred_past_delivery_midpoint",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "entry": plan.entry,
                        "delivery_source": active.source_pivot_price,
                        "delivery_midpoint": midpoint,
                        "delivery_target": active.target_price,
                        "rule_provenance": PROXIMAL_DELIVERY_CONTINUATION_RULE,
                    }
                )
                continue
            if not target_within_draw:
                self._dinc("continuation_target_projected_through_external_draw")
                continue
            eligible.append(plan)
        return super()._route_continuation(eligible)

    @property
    def diagnostics(self):  # type: ignore[no-untyped-def]
        output = dict(super().diagnostics)
        output["proximal_delivery_continuation"] = {
            "entry": "SOURCE_SIDE_OF_EXTERNAL_DELIVERY_MIDPOINT",
            "target": "NO_FARTHER_THAN_MATCHING_SCALE_DRAW",
            "rules": (
                PROXIMAL_DELIVERY_CONTINUATION_RULE,
                EXTERNAL_DRAW_TARGET_CAP_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelAcceptanceV3Bundle
