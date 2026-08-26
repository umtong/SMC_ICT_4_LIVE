"""Broad-context ownership for the lower-scale efficient pullback family.

A complete fifteen-minute acceptance can legitimately reverse a slower context,
but the ordinary five-minute continuation family is a lower-scale mechanism.
When it points against an already accepted sixty-minute leg, the trade therefore
needs contemporaneous BTC/ETH-led common initiative in its own direction.  This
keeps routine pullbacks aligned with the broad auction while allowing genuine
fast transitions to lead the slower structure.

Only efficient-pullback routing changes.  Rejection, OB/FVG continuation,
horizontal flips and mature diagonal acceptance retain their existing
mechanism-specific context responsibilities.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from easychart_re1_efficient_pullback_final import (
    EasyChartRE1EfficientPullbackFinalBundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy


EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "FIVE_MINUTE_EFFICIENT_PULLBACK_ALIGNS_WITH_ACCEPTED_SIXTY_MINUTE_DIRECTION_OR_REQUIRES_SAME_SIDE_ACTIVE_COMMON_INITIATIVE"
)
if EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE,)


class EasyChartRE1ContextualEfficientPullbackBundle(
    EasyChartRE1EfficientPullbackFinalBundle,
):
    """Current-leg efficient pullback with mechanism-appropriate broad context."""

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
                    "scenario_kind": "contextual_efficient_pullback_plan_allowed",
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
                    "rule_provenance": EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE,
                }
            )
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["efficient_pullback_broad_context"] = {
            "rule_provenance": EFFICIENT_PULLBACK_BROAD_CONTEXT_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ContextualEfficientPullbackBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
