"""Broad causal EasyChart candidate generation for ML2.

The deterministic EasyChart state machines still own market location, direction,
entry, structural invalidation, objective and causal-episode identity.  ML2 only
removes broad context vetoes which otherwise destroy complete counterfactual
plans before a model can observe them.  The true context remains available as a
feature and the inherited shadow policy can still be reconstructed exactly.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from easychart_re1_auction_router_v3 import MatureDiagonalResponseFamily
from easychart_re1_complete_bot_policy_v2 import EasyChartRE1CompleteBotPolicyV2Bundle
from easychart_re1_efficient_pullback_context import EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE


ML2_CANDIDATE_SELECTION_SEPARATION_RULE = (
    "RESEARCH_HYPOTHESIS:STRUCTURAL_SCENARIOS_CREATE_FROZEN_CANDIDATE_PLANS_WHILE_"
    "MACRO_COMMON_FLOW_ZONE_FRESHNESS_AND_OBJECTIVE_PATH_ARE_ESTIMATED_BY_A_"
    "CAUSAL_PROBABILITY_SELECTOR"
)
ML2_PREPLAN_CONTEXT_OBSERVATION_RULE = (
    "RESEARCH_IMPLEMENTATION:THE_LIVE_COMMON_FACTOR_REMAINS_AN_OBSERVED_"
    "DECISION_FEATURE_BUT_CANNOT_DESTROY_A_STRUCTURALLY_COMPLETE_COUNTERFACTUAL_"
    "BEFORE_MODEL_SCORING"
)
ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE = (
    "IMPLEMENTATION_REPAIR:MATURE_DIAGONAL_ACCEPTANCE_CONSUMES_ONLY_15_5_1_MINUTE_BARS;"
    "ENCLOSING_60_MINUTE_CONTEXT_BAR_IS_NOT_FORWARDED_TO_THE_MICRO_ENGINE"
)
for _rule in (
    ML2_CANDIDATE_SELECTION_SEPARATION_RULE,
    ML2_PREPLAN_CONTEXT_OBSERVATION_RULE,
    ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class ML2MatureDiagonalResponseFamily(MatureDiagonalResponseFamily):
    """Enforce the 15/5/1 contract of the local diagonal engine."""

    SUPPORTED_TIMEFRAMES = frozenset((15, 5, 1))

    def on_bar(self, timeframe_minutes: int, bar: Any):  # type: ignore[no-untyped-def]
        if timeframe_minutes not in self.SUPPORTED_TIMEFRAMES:
            self._inc("ignored_unsupported_timeframe")
            return []
        return super().on_bar(timeframe_minutes, bar)


class EasyChartML2CandidateBundle(EasyChartRE1CompleteBotPolicyV2Bundle):
    """Complete RE1 opportunity set without broad-context sample censoring."""

    _BROAD_FACTOR_VETO_ENGINES = (
        "local_continuation",
        "efficient_pullback",
        "macro_trend_pullback",
    )

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        # The parent bundle also receives 60-minute context bars.  Its local
        # diagonal source is explicitly a 15/5/1 engine.
        self.mature_diagonal_acceptance = ML2MatureDiagonalResponseFamily(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    def set_market_factor_state(self, state: Any | None) -> None:
        """Keep the true factor observable while neutralizing only broad vetoes.

        ``super`` first publishes the real state to every inherited component,
        including any scenario whose very definition is factor-created.  We then
        neutralize only engines where the factor was an additional quality gate,
        plus the final bundle router.  Structural state, first-return ownership,
        target freshness and duplicate causal-episode ownership are untouched.
        """

        self._ml2_observed_factor_state = state
        super().set_market_factor_state(state)
        self._factor_state = None
        self._market_factor_state = None
        for name in self._BROAD_FACTOR_VETO_ENGINES:
            engine = getattr(self, name, None)
            setter = getattr(engine, "set_market_factor_state", None)
            if setter is not None:
                setter(None)

    def _context_allows(self, plan: V5TradePlan, prefix: str) -> bool:
        counter = getattr(self, prefix)
        macro_side = getattr(self, "_macro_side", None)
        factor = getattr(self, "_ml2_observed_factor_state", None)
        if macro_side is None:
            counter("ml2_candidate_macro_neutral")
        elif macro_side is plan.side:
            counter("ml2_candidate_macro_aligned")
        else:
            counter("ml2_candidate_macro_opposed")
        if factor is None:
            counter("ml2_candidate_factor_neutral")
        elif factor.side is plan.side:
            counter("ml2_candidate_factor_aligned")
        else:
            counter("ml2_candidate_factor_opposed")
        counter("ml2_candidate_context_deferred_to_selector")
        return True

    def _route_pullback(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._pinc("efficient_pullback_duplicate_episode")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._pinc("ml2_pullback_candidate_exposed")
            macro_side = getattr(self, "_macro_side", None)
            factor = getattr(self, "_ml2_observed_factor_state", None)
            self._pullback_trace.append(
                {
                    "scenario_kind": "ml2_context_deferred_pullback_candidate",
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
                        ML2_CANDIDATE_SELECTION_SEPARATION_RULE,
                    ),
                },
            )
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        factor = getattr(self, "_ml2_observed_factor_state", None)
        output["ml2_candidate_generation"] = {
            "policy": (
                "KEEP_CAUSAL_SCENARIO_GEOMETRY_AND_DUPLICATE_EPISODE_OWNERSHIP; "
                "OBSERVE_BUT_DO_NOT_PRE_CENSOR_BY_BROAD_COMMON_FACTOR; "
                "DEFER_BROAD_CONTEXT_QUALITY_TO_CALIBRATED_SELECTOR"
            ),
            "observed_factor_side": None if factor is None else factor.side.name,
            "neutralized_preplan_veto_engines": self._BROAD_FACTOR_VETO_ENGINES,
            "mature_diagonal_supported_timeframes": (15, 5, 1),
            "rules": (
                ML2_CANDIDATE_SELECTION_SEPARATION_RULE,
                ML2_PREPLAN_CONTEXT_OBSERVATION_RULE,
                ML2_DIAGONAL_TIMEFRAME_CONTRACT_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartML2CandidateBundle
