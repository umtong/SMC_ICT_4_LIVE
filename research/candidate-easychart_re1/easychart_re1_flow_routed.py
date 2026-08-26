"""Mechanism-aware routing for the causal EasyChart RE1 flow candidate.

The first flow engine correctly created initiative and absorption plans inside
an already meaningful structure episode, but the inherited bundle router still
applied the old responsibilities afterwards:

* every isolated MICRO acceptance was deferred, including one whose trigger was
  coherent taker initiative;
* decision-area reversals still required the old local-BOS alignment even when
  the entry event was direct boundary absorption;
* a flow plan rejected by a stale structural side could not express that the
  order-flow event itself was the reversal evidence.

This module fixes only that responsibility mismatch. Ordinary OB/FVG and exact
retest plans keep every inherited rule. Flow is an OR branch only when the
trade plan's trigger kind explicitly records initiative or absorption. No
numeric score, fitted threshold, clock filter, risk multiplier, partial exit or
post-entry management rule is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from easychart_re1 import EasyChartRE1Bundle
from easychart_re1_flow import EasyChartRE1FlowBundle, FlowTriggerKind


FLOW_ROUTER_RESPONSIBILITY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "A_PLAN_EXPLICITLY_TRIGGERED_BY_COHERENT_INITIATIVE_OR_BOUNDARY_ABSORPTION_IS_ROUTED_BY_THAT_MECHANISM_NOT_BY_THE_OLD_MISSING_FOOTPRINT_GATE"
)
FLOW_ABSORPTION_REVERSAL_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "BOUNDARY_ABSORPTION_CAN_OWN_A_LOCAL_REVERSAL_EPISODE_AGAINST_A_LAGGING_STRUCTURAL_DIRECTION"
)
for _rule in (FLOW_ROUTER_RESPONSIBILITY_RULE, FLOW_ABSORPTION_REVERSAL_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


_INITIATIVE_KINDS = {
    FlowTriggerKind.BUY_INITIATIVE.value,
    FlowTriggerKind.SELL_INITIATIVE.value,
}
_ABSORPTION_KINDS = {
    FlowTriggerKind.SELL_ABSORPTION.value,
    FlowTriggerKind.BUY_ABSORPTION.value,
    FlowTriggerKind.REPEATED_SELL_ABSORPTION.value,
    FlowTriggerKind.REPEATED_BUY_ABSORPTION.value,
}


class EasyChartRE1FlowRoutedBundle(EasyChartRE1FlowBundle):
    """Complete fixed-plan system whose flow entries reach the account router."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._flow_route_counts: dict[str, int] = {}
        self._flow_route_trace: list[dict[str, Any]] = []

    def _frinc(self, key: str) -> None:
        self._flow_route_counts[key] = self._flow_route_counts.get(key, 0) + 1

    @staticmethod
    def _trigger_kind(plan: V5TradePlan) -> str:
        return str(getattr(plan.trigger_zone_kind, "value", plan.trigger_zone_kind))

    @classmethod
    def _initiative_plan(cls, plan: V5TradePlan) -> bool:
        return cls._trigger_kind(plan) in _INITIATIVE_KINDS

    @classmethod
    def _absorption_plan(cls, plan: V5TradePlan) -> bool:
        return cls._trigger_kind(plan) in _ABSORPTION_KINDS

    @classmethod
    def _flow_plan(cls, plan: V5TradePlan) -> bool:
        return cls._initiative_plan(plan) or cls._absorption_plan(plan)

    def _record_flow_route(self, kind: str, plan: V5TradePlan, **values: Any) -> None:
        self._flow_route_trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "setup_id": plan.setup_id,
                "side": plan.side.name,
                "scenario_path": plan.scenario_path,
                "scale_name": plan.scale_name,
                "trigger_zone_kind": self._trigger_kind(plan),
                "interaction_time_ns": plan.interaction_time_ns,
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "gross_rr": plan.gross_rr,
                **values,
            }
        )

    def _route_plan(self, plan: V5TradePlan) -> bool:
        initiative = self._initiative_plan(plan)
        absorption = self._absorption_plan(plan)

        # The old complete-policy router deferred every generic MICRO
        # acceptance because no flow mechanism existed at the time. Coherent
        # initiative is now that missing mechanism, but macro neutral/aligned
        # context is still required for this first implementation.
        if (
            plan.scale_name == "MICRO"
            and plan.scenario_path == ScenarioPath.ACCEPTANCE.value
            and initiative
        ):
            allowed = EasyChartRE1Bundle._route_plan(self, plan)
            key = (
                "micro_acceptance_initiative_routed"
                if allowed
                else "micro_acceptance_initiative_rejected_by_macro_context"
            )
            self._frinc(key)
            self._record_flow_route(
                key,
                plan,
                allowed=allowed,
                rule_provenance=FLOW_ROUTER_RESPONSIBILITY_RULE,
            )
            return allowed

        inherited = super()._route_plan(plan)
        if inherited:
            if self._flow_plan(plan):
                self._frinc("flow_plan_allowed_by_inherited_router")
            return True

        # Opposing aggressive orders which fail to move price through the
        # boundary and are followed by a reclaim are the reversal evidence;
        # requiring the lagging BOS router to agree would erase that mechanism.
        if (
            absorption
            and plan.scenario_path
            in {ScenarioPath.REJECTION.value, ScenarioPath.BOUNCE.value}
        ):
            self._frinc("absorption_reversal_rescued_after_structural_rejection")
            self._record_flow_route(
                "absorption_reversal_rescued_after_structural_rejection",
                plan,
                allowed=True,
                rule_provenance=FLOW_ABSORPTION_REVERSAL_RULE,
            )
            return True

        return False

    def _route_decision_area(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path not in {
                ScenarioPath.BOUNCE.value,
                ScenarioPath.REJECTION.value,
            }:
                self._cinc("decision_ob_non_bounce_or_sweep_suppressed")
                continue

            initiative = self._initiative_plan(plan)
            absorption = self._absorption_plan(plan)
            flow_supported = initiative or absorption
            aligned = (
                self._local_side is not None
                and self._local_side is plan.side
                and self._local_break_time_ns is not None
                and plan.observed_time_ns >= self._local_break_time_ns
            )
            if not aligned and not flow_supported:
                self._cinc("decision_ob_deferred_against_local_structure")
                continue

            inherited = self._route_plan(plan)
            allowed = inherited or flow_supported
            if not allowed:
                self._frinc("decision_area_rejected_without_context_or_flow")
                continue
            if self._duplicate_episode(plan):
                self._cinc("decision_ob_overlapped_existing_family")
                continue

            self._claim_episode(plan)
            output.append(plan)
            self._cinc("decision_ob_plan_allowed")
            if flow_supported and not aligned:
                self._frinc("decision_area_recovered_by_flow")
                self._record_flow_route(
                    "decision_area_recovered_by_flow",
                    plan,
                    local_side=(
                        "NEUTRAL" if self._local_side is None else self._local_side.name
                    ),
                    rule_provenance=(
                        FLOW_ROUTER_RESPONSIBILITY_RULE,
                        FLOW_ABSORPTION_REVERSAL_RULE,
                    ),
                )
        return output

    def _route_horizontal_flip(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path != ScenarioPath.ACCEPTANCE.value:
                self._cinc("horizontal_flip_non_acceptance_suppressed")
                continue

            initiative = self._initiative_plan(plan)
            inherited = self._route_plan(plan)
            allowed = inherited or initiative
            if not allowed:
                self._frinc("horizontal_flip_rejected_without_context_or_initiative")
                continue
            if self._duplicate_episode(plan):
                self._cinc("horizontal_flip_overlapped_existing_family")
                continue

            self._claim_episode(plan)
            output.append(plan)
            self._cinc("horizontal_flip_plan_allowed")
            if initiative and not inherited:
                self._frinc("horizontal_flip_recovered_by_initiative")
                self._record_flow_route(
                    "horizontal_flip_recovered_by_initiative",
                    plan,
                    rule_provenance=FLOW_ROUTER_RESPONSIBILITY_RULE,
                )
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self._flow_route_trace
        self._flow_route_trace = []
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["mechanism_aware_flow_router"] = {
            "counts": dict(sorted(self._flow_route_counts.items())),
            "initiative_kinds": tuple(sorted(_INITIATIVE_KINDS)),
            "absorption_kinds": tuple(sorted(_ABSORPTION_KINDS)),
            "rules": (
                FLOW_ROUTER_RESPONSIBILITY_RULE,
                FLOW_ABSORPTION_REVERSAL_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FlowRoutedBundle
