"""Wide but still causal EasyChart candidate generator for ML1.

RE1 mixed two responsibilities: detecting a structurally valid opportunity and
hard-coding whether its broad context was good enough.  ML1 keeps every existing
mechanism, immutable entry/stop/target and duplicate-episode ownership, but moves
macro/common-factor quality routing to the calibrated selector.  This exposes
counterfactual examples that the former boolean router could never learn from.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from easychart_re1_complete_bot_policy_v2 import EasyChartRE1CompleteBotPolicyV2Bundle
from easychart_re1_efficient_pullback_context import EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE


ML1_CANDIDATE_SELECTION_SEPARATION_RULE = (
    "RESEARCH_HYPOTHESIS:STRUCTURAL_MECHANISMS_CREATE_FROZEN_CANDIDATE_PLANS_WHILE_"
    "MACRO_COMMON_FLOW_AND_GEOMETRY_CONTEXT_ARE_ESTIMATED_JOINTLY_BY_A_CAUSAL_CALIBRATED_SELECTOR"
)
if ML1_CANDIDATE_SELECTION_SEPARATION_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (ML1_CANDIDATE_SELECTION_SEPARATION_RULE,)


class EasyChartML1CandidateBundle(EasyChartRE1CompleteBotPolicyV2Bundle):
    """RE1 complete opportunity set without deterministic context suppression."""

    def _context_allows(self, plan: V5TradePlan, prefix: str) -> bool:
        counter = getattr(self, prefix)
        macro_side = getattr(self, "_macro_side", None)
        factor = getattr(self, "_factor_state", None)
        if macro_side is None:
            counter("ml1_candidate_macro_neutral")
        elif macro_side is plan.side:
            counter("ml1_candidate_macro_aligned")
        else:
            counter("ml1_candidate_macro_opposed")
        if factor is None:
            counter("ml1_candidate_factor_neutral")
        elif factor.side is plan.side:
            counter("ml1_candidate_factor_aligned")
        else:
            counter("ml1_candidate_factor_opposed")
        counter("ml1_candidate_context_deferred_to_selector")
        return True

    def _route_pullback(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._pinc("efficient_pullback_duplicate_episode")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._pinc("ml1_pullback_candidate_exposed")
            macro_side = getattr(self, "_macro_side", None)
            factor = getattr(self, "_factor_state", None)
            self._pullback_trace.append(
                {
                    "scenario_kind": "ml1_context_deferred_pullback_candidate",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": None if macro_side is None else macro_side.name,
                    "factor_side": None if factor is None else factor.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE,
                        ML1_CANDIDATE_SELECTION_SEPARATION_RULE,
                    ),
                }
            )
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["ml1_candidate_generation"] = {
            "policy": (
                "KEEP_CAUSAL_MECHANISM_AND_DUPLICATE_EPISODE_OWNERSHIP; "
                "DEFER_MACRO_COMMON_FACTOR_AND_FLOW_QUALITY_TO_ML_SELECTOR"
            ),
            "rule": ML1_CANDIDATE_SELECTION_SEPARATION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartML1CandidateBundle
