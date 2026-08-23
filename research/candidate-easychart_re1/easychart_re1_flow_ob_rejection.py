"""Liquidity-event-only routing for flow-validated 15-minute order blocks.

The source material does not treat every first touch of an order block as the
same event.  The institutional explanation is strongest when the touch also
creates or consumes liquidity: price crosses the pre-existing boundary, stops
and breakout orders trade, the opposing aggression is absorbed, and price
reclaims the area.  A simple bounce which never sweeps the boundary has no such
observable inventory-transfer event.

The parent candidate already validates the *birth* of each 15-minute engulfing
OB with aligned one-minute taker flow and price progress.  This policy changes
only the later interaction responsibility:

* REJECTION keeps the flow-validated OB family because it contains an explicit
  sweep/reclaim or event-local reversal footprint;
* BOUNCE is suppressed for this family rather than being rescued with another
  threshold or score;
* the ordered-channel phase-flow core and all account, risk, cost and natural
  stop/target geometry remain unchanged.

This is a mechanism split, not a fitted family-performance filter.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from easychart_re1_flow_ob import EasyChartRE1PhaseFlowOBBundle


FLOW_OB_LIQUIDITY_EVENT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_FLOW_VALIDATED_FIFTEEN_MINUTE_OB_ORIGINATES_A_TRADE_ONLY_WHEN_THE_FIRST_LATER_INTERACTION_IS_AN_EXPLICIT_SWEEP_RECLAIM_REJECTION_NOT_AN_ORDINARY_BOUNCE"
)
if FLOW_OB_LIQUIDITY_EVENT_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FLOW_OB_LIQUIDITY_EVENT_RULE,)


class EasyChartRE1PhaseFlowOBRejectionBundle(EasyChartRE1PhaseFlowOBBundle):
    """Ordered phase flow plus flow-born OBs at explicit liquidity rejections."""

    def _route_flow_ob(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        rejection: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path != ScenarioPath.REJECTION.value:
                self._oinc("flow_ob_non_liquidity_bounce_suppressed")
                self._flow_ob_trace.append(
                    {
                        "scenario_kind": "flow_validated_ob_bounce_suppressed_without_liquidity_event",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "scenario_path": plan.scenario_path,
                        "entry": plan.entry,
                        "stop": plan.stop,
                        "target": plan.target,
                        "gross_rr": plan.gross_rr,
                        "rule_provenance": FLOW_OB_LIQUIDITY_EVENT_RULE,
                    }
                )
                continue
            rejection.append(plan)
        return super()._route_flow_ob(rejection)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["flow_validated_decision_ob_liquidity_event"] = {
            "executable_path": ScenarioPath.REJECTION.value,
            "suppressed_path": ScenarioPath.BOUNCE.value,
            "rule_provenance": FLOW_OB_LIQUIDITY_EVENT_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1PhaseFlowOBRejectionBundle
