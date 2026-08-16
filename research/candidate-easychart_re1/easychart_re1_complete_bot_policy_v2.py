"""One broad-context rule for every continuation family.

The complete policy already gave the lower-scale efficient pullback the proper
context responsibility: follow the accepted sixty-minute leg, or require a live
BTC/ETH-led common impulse to lead a faster transition.  Event-local OB/FVG,
horizontal flip and mature diagonal continuation were still allowed to override
the broad leg without that transition evidence.

This module makes continuation routing consistent:

* macro-aligned or macro-neutral continuation is executable;
* counter-macro continuation is executable only while common initiative agrees;
* an active opposite common impulse vetoes every continuation;
* rejection/reversal keeps its own higher-frame decision-area logic;
* residual macro-trend pullback remains naturally aligned by construction.

The rule does not turn context into another pattern score.  It assigns one
responsibility to the broad auction and leaves entry, stop, objective and account
logic unchanged.  Frequency is supplied by the independent macro pullback family,
not by allowing low-context countertrend continuations.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from easychart_re1_complete_bot_policy import (
    COMPLETE_OPPORTUNITY_ROUTER_RULE,
    EasyChartRE1CompleteBotPolicyBundle,
)
from easychart_re1_horizontal_flip_response import (
    HORIZONTAL_FLIP_RESPONSE_RULE,
    HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
)
from easychart_re1_local_auction_continuation import (
    COMMON_FACTOR_VETO_ONLY_RULE,
    LOCAL_AUCTION_CONTINUATION_RULE,
    SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
    EasyChartRE1LocalAuctionStrategy,
)
from easychart_re1_local_auction_continuation_v2 import LOCAL_CONTINUATION_RESPONSE_FLOW_RULE
from easychart_re1_auction_router_v3 import (
    MATURE_DIAGONAL_ACCEPTANCE_RULE,
    MATURE_DIAGONAL_OBJECTIVE_RULE,
)


UNIFIED_CONTINUATION_CONTEXT_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "EVERY_LOCAL_CONTINUATION_ALIGNS_WITH_ACCEPTED_SIXTY_MINUTE_DIRECTION_OR_REQUIRES_SAME_SIDE_LIVE_COMMON_INITIATIVE"
)
if UNIFIED_CONTINUATION_CONTEXT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (UNIFIED_CONTINUATION_CONTEXT_RULE,)


class EasyChartRE1CompleteBotPolicyV2Bundle(EasyChartRE1CompleteBotPolicyBundle):
    """Complete opportunity set under one coherent continuation context router."""

    def _context_allows(self, plan: V5TradePlan, prefix: str) -> bool:
        factor = self._factor_state
        macro_side = getattr(self, "_macro_side", None)
        if factor is not None and factor.side is not plan.side:
            getattr(self, prefix)("continuation_rejected_by_opposing_common_factor")
            return False
        if macro_side is None or macro_side is plan.side:
            getattr(self, prefix)("continuation_macro_aligned_or_neutral")
            return True
        if factor is not None and factor.side is plan.side:
            getattr(self, prefix)("counter_macro_continuation_allowed_by_common_initiative")
            return True
        getattr(self, prefix)("counter_macro_continuation_without_common_initiative")
        return False

    def _route_local_continuation(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.symbol,
                item.plan_id,
            ),
        ):
            if plan.scenario_path != "ACCEPTANCE":
                self._binc("non_acceptance_local_continuation_suppressed")
                continue
            if self._duplicate_episode(plan):
                self._binc("local_continuation_duplicate_episode")
                continue
            if not self._context_allows(plan, "_binc"):
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._binc("local_continuation_plan_allowed")
            self._local_bundle_trace.append(
                {
                    "scenario_kind": "unified_context_local_ob_fvg_continuation_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": None if self._macro_side is None else self._macro_side.name,
                    "factor_side": None if self._factor_state is None else self._factor_state.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        LOCAL_AUCTION_CONTINUATION_RULE,
                        LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
                        SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
                        UNIFIED_CONTINUATION_CONTEXT_RULE,
                    ),
                }
            )
        return output

    def _route_horizontal(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._hinc("horizontal_flip_duplicate_episode")
                continue
            if not self._context_allows(plan, "_hinc"):
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._hinc("horizontal_flip_plan_allowed")
            self._horizontal_trace.append(
                {
                    "scenario_kind": "unified_context_horizontal_flip_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": None if self._macro_side is None else self._macro_side.name,
                    "factor_side": None if self._factor_state is None else self._factor_state.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        HORIZONTAL_FLIP_RESPONSE_RULE,
                        HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
                        UNIFIED_CONTINUATION_CONTEXT_RULE,
                    ),
                }
            )
        return output

    def _route_diagonal(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._dinc("mature_diagonal_duplicate_episode")
                continue
            if not self._context_allows(plan, "_dinc"):
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._dinc("mature_diagonal_plan_allowed")
            self._diagonal_trace.append(
                {
                    "scenario_kind": "unified_context_mature_diagonal_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": None if self._macro_side is None else self._macro_side.name,
                    "factor_side": None if self._factor_state is None else self._factor_state.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        MATURE_DIAGONAL_ACCEPTANCE_RULE,
                        MATURE_DIAGONAL_OBJECTIVE_RULE,
                        UNIFIED_CONTINUATION_CONTEXT_RULE,
                    ),
                }
            )
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["unified_continuation_context"] = {
            "policy": (
                "MACRO_ALIGNED_OR_NEUTRAL; COUNTER_MACRO_ONLY_WITH_SAME_SIDE_LIVE_COMMON_INITIATIVE; "
                "ACTIVE_OPPOSITE_COMMON_INITIATIVE_VETOES"
            ),
            "rules": (
                COMMON_FACTOR_VETO_ONLY_RULE,
                COMPLETE_OPPORTUNITY_ROUTER_RULE,
                UNIFIED_CONTINUATION_CONTEXT_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1CompleteBotPolicyV2Bundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
