"""Complete EasyChart auction policy for the one-account day-trading bot.

This module combines the two complementary changes which solve the remaining
quality/frequency tension without weakening any setup:

* the lower-scale fifteen-minute-leg pullback follows the accepted sixty-minute
  leg unless a live BTC/ETH-led common impulse supports the faster transition;
* an established accepted sixty-minute trend independently supplies residual
  five-minute first-pullback opportunities when no OB/FVG, horizontal, local-leg
  or mature diagonal owner has claimed the episode.

The final opportunity order is therefore:

1. responsible rejection/reversal;
2. event-local OB/FVG continuation;
3. horizontal S/R flip;
4. accepted local-leg efficient pullback with mechanism-specific broad context;
5. residual accepted macro-trend efficient pullback;
6. residual mature diagonal/channel acceptance.

Every continuation still requires a body break, the immediate next-bar hold,
the first return, and the first flow-confirmed micro response.  The first target
is frozen before entry, the response excursion owns invalidation, gross planned
RR must remain at least 1R, and all scenarios compete for the same global
position.  Frequency comes from independent auctions, not weaker evidence.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from easychart_re1_efficient_pullback_context import (
    EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE,
)
from easychart_re1_macro_trend_pullback import (
    MACRO_TREND_PULLBACK_LIFECYCLE_RULE,
    MACRO_TREND_PULLBACK_RULE,
    EasyChartRE1MacroTrendOpportunityBundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy


COMPLETE_OPPORTUNITY_ROUTER_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "LOCAL_PULLBACK_USES_MACRO_ALIGNMENT_OR_LIVE_COMMON_SUPPORT_WHILE_RESIDUAL_MACRO_PULLBACK_EXPANDS_THE_OPPORTUNITY_SET_WITHOUT_DUPLICATING_A_CAUSAL_EPISODE"
)
if COMPLETE_OPPORTUNITY_ROUTER_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (COMPLETE_OPPORTUNITY_ROUTER_RULE,)


class EasyChartRE1CompleteBotPolicyBundle(EasyChartRE1MacroTrendOpportunityBundle):
    """One coherent auction router balancing high accuracy and real frequency."""

    def _route_pullback(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._pinc("efficient_pullback_duplicate_episode")
                continue
            factor = self._factor_state
            if factor is not None and factor.side is not plan.side:
                self._pinc("efficient_pullback_rejected_by_opposing_common_factor")
                continue
            macro_side = getattr(self, "_macro_side", None)
            if macro_side is not None and macro_side is not plan.side:
                if factor is None or factor.side is not plan.side:
                    self._pinc("counter_macro_pullback_without_common_initiative")
                    continue
                self._pinc("counter_macro_pullback_allowed_by_common_initiative")
            else:
                self._pinc("macro_aligned_or_neutral_pullback")
            self._claim_episode(plan)
            output.append(plan)
            self._pinc("efficient_pullback_plan_allowed")
            self._pullback_trace.append(
                {
                    "scenario_kind": "complete_policy_local_pullback_allowed",
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
                        COMPLETE_OPPORTUNITY_ROUTER_RULE,
                    ),
                }
            )
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["complete_opportunity_router"] = {
            "owners": (
                "REJECTION",
                "EVENT_LOCAL_OB_FVG_CONTINUATION",
                "HORIZONTAL_SR_FLIP",
                "CONTEXTUAL_LOCAL_EFFICIENT_PULLBACK",
                "RESIDUAL_MACRO_TREND_PULLBACK",
                "RESIDUAL_MATURE_DIAGONAL_ACCEPTANCE",
            ),
            "rules": (
                EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE,
                MACRO_TREND_PULLBACK_RULE,
                MACRO_TREND_PULLBACK_LIFECYCLE_RULE,
                COMPLETE_OPPORTUNITY_ROUTER_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1CompleteBotPolicyBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
