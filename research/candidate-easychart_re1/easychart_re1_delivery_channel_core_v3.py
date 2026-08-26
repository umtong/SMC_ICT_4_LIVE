"""Lean core with explicit responsibility for executable rejection families.

Generic one-minute-confirmed rejection of every local trend line or channel edge
remains too ambiguous even when its direction agrees with external delivery.
Channel state change already has a dedicated accepted-break/retest family, while
a pullback continuation already has a dedicated flow-validated OB/FVG family.
Letting the generic MICRO engine also trade rejection duplicates both roles and
reintroduces weak diagonal fades.

Executable rejection is therefore limited to a meaningful decision-area OB,
direct sweep OB or major-liquidity interaction.  The MICRO engine remains fully
observable in diagnostics but cannot send a plan to the account.  No numeric
threshold, score, symbol rule or performance lookup is used.
"""
from __future__ import annotations

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from easychart_re1_delivery_channel_core_v2 import (
    EasyChartRE1DeliveryChannelCoreV2Bundle,
)


GENERIC_MICRO_REJECTION_DEFERRED_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "GENERIC_LOCAL_DIAGONAL_REJECTION_REMAINS_DIAGNOSTIC_WHILE_DECISION_OB_DIRECT_SWEEP_AND_MAJOR_LIQUIDITY_OWN_EXECUTABLE_REJECTION"
)
if GENERIC_MICRO_REJECTION_DEFERRED_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (GENERIC_MICRO_REJECTION_DEFERRED_RULE,)


class EasyChartRE1DeliveryChannelCoreV3Bundle(
    EasyChartRE1DeliveryChannelCoreV2Bundle,
):
    EXECUTABLE_REJECTION_SCALES = {
        "FLOW_DECISION_OB",
        "DIRECT_SWEEP_OB",
        "LIQUIDITY",
    }

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if plan.scale_name not in self.EXECUTABLE_REJECTION_SCALES:
            self._dinc("generic_micro_rejection_deferred")
            self._delivery_trace.append(
                {
                    "scenario_kind": "generic_micro_rejection_deferred",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "scale_name": plan.scale_name,
                    "scenario_path": plan.scenario_path,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "rule_provenance": GENERIC_MICRO_REJECTION_DEFERRED_RULE,
                }
            )
            return False
        return super()._route_plan(plan)

    @property
    def diagnostics(self):  # type: ignore[no-untyped-def]
        output = dict(super().diagnostics)
        output["executable_rejection_responsibility"] = {
            "allowed_scales": tuple(sorted(self.EXECUTABLE_REJECTION_SCALES)),
            "micro": "DIAGNOSTIC_ONLY",
            "rule_provenance": GENERIC_MICRO_REJECTION_DEFERRED_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DeliveryChannelCoreV3Bundle
