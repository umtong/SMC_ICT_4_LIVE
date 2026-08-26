"""Rejection-only responsibility for flow-validated 15-minute decision OBs.

A flow-validated engulfing OB proves that real initiative created a decision
area.  It does not make a later blind first-touch bounce equivalent to a
completed liquidity-take-and-reclaim episode.  The supplied OB/FVG material
repeatedly strengthens footprints when they occur at liquidity absorption or
structure, and the Fakeout/Trap material requires return and confirmation.

The current integrated family nevertheless allowed both ``BOUNCE`` and
``REJECTION`` paths.  Expanded diagnostics showed blind OB bounces were a small
but consistently losing family.  This module changes only the scenario
responsibility:

* the original 15-minute OB birth still requires aligned constituent taker flow
  and net price progress;
* a later flow-valid decision OB can execute only after sweep/reclaim rejection;
* bounce setups remain recorded for diagnosis but cannot submit account plans;
* channel reversals remain diagnostic-only; all visual entry ownership, stops,
  objectives, fees and one-position routing are unchanged.

No score, magnitude threshold, clock expiry, symbol rule or outcome-dependent
selection is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from easychart_re1_channel_abstention import EasyChartRE1ChannelAbstentionBundle


FLOW_VALIDATED_OB_REJECTION_ONLY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FLOW_VALIDATED_FIFTEEN_MINUTE_DECISION_OB_EXECUTES_ONLY_AFTER_LIQUIDITY_SWEEP_AND_RECLAIM_REJECTION_NOT_BLIND_BOUNCE"
)
if FLOW_VALIDATED_OB_REJECTION_ONLY_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FLOW_VALIDATED_OB_REJECTION_ONLY_RULE,)


class EasyChartRE1DecisionOBRejectionBundle(EasyChartRE1ChannelAbstentionBundle):
    """Quality reversal core with rejection-only flow-valid 15m OB routing."""

    def _route_flow_ob(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        rejection: list[V5TradePlan] = []
        for plan in raw:
            if plan.scenario_path == ScenarioPath.BOUNCE.value:
                self._oinc("flow_validated_ob_blind_bounce_diagnostic_only")
                self._flow_ob_trace.append(
                    {
                        "scenario_kind": "flow_validated_ob_blind_bounce_diagnostic_only",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "scenario_path": plan.scenario_path,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "entry": plan.entry,
                        "stop": plan.stop,
                        "target": plan.target,
                        "gross_rr": plan.gross_rr,
                        "rule_provenance": FLOW_VALIDATED_OB_REJECTION_ONLY_RULE,
                    }
                )
                continue
            rejection.append(plan)
        return super()._route_flow_ob(rejection)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["flow_validated_decision_ob_rejection_only"] = {
            "executable_paths": (ScenarioPath.REJECTION.value,),
            "bounce": "DIAGNOSTIC_ONLY",
            "rule_provenance": FLOW_VALIDATED_OB_REJECTION_ONLY_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DecisionOBRejectionBundle
