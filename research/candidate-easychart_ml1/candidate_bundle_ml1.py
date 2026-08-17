"""Causal EasyChart candidate generator for ML1.

RE1 mixed structural opportunity detection with broad-context boolean routing.
ML1 keeps the existing mechanisms, frozen entry/stop/target and causal-episode
ownership, while exposing the context variables to the probability model.

The candidate set is not widened by weakening stops, targets or RR.  It is widened
only by recording structurally complete plans which a broad-context boolean gate
would otherwise hide.  Whether those plans are genuinely better is learned from
their target-before-stop outcomes, not assumed.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_auction_router_v3 import MatureDiagonalResponseFamily
from easychart_re1_complete_bot_policy_v2 import EasyChartRE1CompleteBotPolicyV2Bundle
from easychart_re1_efficient_pullback_context import EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE


ML1_CANDIDATE_SELECTION_SEPARATION_RULE = (
    "RESEARCH_HYPOTHESIS:STRUCTURAL_MECHANISMS_CREATE_FROZEN_CANDIDATE_PLANS_WHILE_"
    "MACRO_COMMON_FLOW_AND_GEOMETRY_CONTEXT_ARE_ESTIMATED_JOINTLY_BY_A_CAUSAL_CALIBRATED_SELECTOR"
)
if ML1_CANDIDATE_SELECTION_SEPARATION_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (ML1_CANDIDATE_SELECTION_SEPARATION_RULE,)


class ML1MatureDiagonalResponseFamily(MatureDiagonalResponseFamily):
    """Feed the 15m/5m/1m engine only the timeframes it was built to consume."""

    _SUPPORTED_TIMEFRAMES = frozenset((1, 5, 15))

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes not in self._SUPPORTED_TIMEFRAMES:
            return []
        return super().on_bar(timeframe_minutes, bar)


class EasyChartML1CandidateBundle(EasyChartRE1CompleteBotPolicyV2Bundle):
    """RE1 complete opportunity set with context recorded for ML judgment."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        # The parent aggregate sends every account timeframe to every family.
        # This family is explicitly a 15m/5m/1m engine; a 60m bar is context for
        # other families, not an input to its HumanMicroEngine.
        self.mature_diagonal_acceptance = ML1MatureDiagonalResponseFamily(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

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
            "mature_diagonal_timeframes": sorted(
                ML1MatureDiagonalResponseFamily._SUPPORTED_TIMEFRAMES,
            ),
            "rule": ML1_CANDIDATE_SELECTION_SEPARATION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartML1CandidateBundle
