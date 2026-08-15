"""Response-confirmed acceptance plus current-event decision-OB rejection.

A flow-validated fifteen-minute engulfing order block proves that the original
impulse contained traded initiative.  It does not prove that the same resting
level is still defended when price revisits it much later.  In the current
implementation the ``BOUNCE`` path could trade that historical first touch
without a new sweep/reclaim episode.  Across every available diagnostic period
that path produced losses and, more importantly, its causal claim differs from
the supplied material: an OB is strongest when it belongs to meaningful
structure or liquidity, while a confirmed fakeout entry requires a present
boundary violation and return.

This policy therefore leaves the visual level and its diagnostics intact but
routes a flow-validated decision OB to the account only through its current
``REJECTION`` path.  The independent micro, horizontal and major-liquidity
families are unchanged.  Accepted breaks use the embedded-ret​est plus first
micro-response policy.  Stops, first structural objectives, minimum 1R,
continuous NAV, costs and one global account slot are inherited unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from easychart_re1_embedded_acceptance_response import (
    EasyChartRE1EmbeddedAcceptanceResponseBundle,
)


DECISION_OB_CURRENT_REJECTION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "A_HISTORICAL_FLOW_VALIDATED_DECISION_OB_IS_EXECUTABLE_ONLY_AFTER_A_CURRENT_SWEEP_RECLAIM_REJECTION_NOT_ON_AN_UNCONFIRMED_FIRST_TOUCH_BOUNCE"
)
if DECISION_OB_CURRENT_REJECTION_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (DECISION_OB_CURRENT_REJECTION_RULE,)


class EasyChartRE1ResponseRejectionCoreBundle(
    EasyChartRE1EmbeddedAcceptanceResponseBundle
):
    """One account stream with no unconfirmed historical decision-OB bounce."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._decision_ob_rejection_counts: dict[str, int] = {}
        self._decision_ob_rejection_trace: list[dict[str, Any]] = []

    def _dinc(self, key: str) -> None:
        self._decision_ob_rejection_counts[key] = (
            self._decision_ob_rejection_counts.get(key, 0) + 1
        )

    def _route_flow_ob(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        eligible: list[V5TradePlan] = []
        for plan in raw:
            if plan.scenario_path == ScenarioPath.BOUNCE.value:
                self._dinc("historical_decision_ob_bounce_deferred")
                self._decision_ob_rejection_trace.append(
                    {
                        "scenario_kind": "historical_decision_ob_bounce_deferred",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "entry": plan.entry,
                        "stop": plan.stop,
                        "target": plan.target,
                        "gross_rr": plan.gross_rr,
                        "rule_provenance": DECISION_OB_CURRENT_REJECTION_RULE,
                    }
                )
                continue
            eligible.append(plan)
        routed = super()._route_flow_ob(eligible)
        for plan in routed:
            if plan.scenario_path == ScenarioPath.REJECTION.value:
                self._dinc("current_decision_ob_rejection_allowed")
        return routed

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self._decision_ob_rejection_trace
        self._decision_ob_rejection_trace = []
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["decision_ob_current_rejection_policy"] = {
            "counts": dict(sorted(self._decision_ob_rejection_counts.items())),
            "historical_bounce_executable": False,
            "current_rejection_executable": True,
            "rule_provenance": DECISION_OB_CURRENT_REJECTION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ResponseRejectionCoreBundle
