"""Mechanism-specific context routing for the integrated EasyChart policy.

A broad direction label and a local continuation scenario answer different
questions.  The earlier integrated candidate accidentally made the one-hour
side a second hard gate for an already complete local continuation, then allowed
an exception only during a rare market-wide shock.  That recreates the same
problem as stacking filters: good local acceptance is discarded because a
slower state has not changed yet.

This router assigns context by mechanism instead:

* rejection/reversal continues to use the acceptance-confirmed one-hour router
  and its higher-frame decision-area exception;
* local 15-minute BOS -> flow-validated five-minute OB -> first-return response
  is self-contained directional evidence and is rejected only while an active
  BTC/ETH-led common impulse is opposite;
* horizontal body-break -> next-bar hold -> first return -> first response is
  the same kind of accepted local auction and uses the same common-factor veto;
* unresolved events still produce no trade, and each causal episode keeps one
  owner.

The change is a responsibility correction, not a looser threshold.  Stops,
objectives, minimum 1R geometry, 3% NAV risk, one global position and immutable
full-position exits are unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
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
from easychart_re1_local_auction_continuation_v2 import (
    LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
)
from easychart_re1_macro_acceptance import (
    MACRO_BREAK_ACCEPTANCE_RULE,
    EasyChartRE1MacroAcceptanceBundle,
)


MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "BROAD_CONTEXT_ROUTES_REJECTIONS_WHILE_COMPLETE_LOCAL_ACCEPTANCE_CONTINUATIONS_REQUIRE_ONLY_NO_ACTIVE_OPPOSING_COMMON_FACTOR"
)
if MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE,)


class EasyChartRE1AuctionRouterBundle(EasyChartRE1MacroAcceptanceBundle):
    """One auction policy with context responsibility determined by mechanism."""

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
            factor = self._market_factor_state
            if factor is not None and factor.side is not plan.side:
                self._binc("local_continuation_rejected_by_opposing_common_factor")
                continue

            macro_side = getattr(self, "_macro_side", None)
            if macro_side is not None and macro_side is not plan.side:
                self._binc("counter_macro_local_acceptance_owned_by_local_auction")
            else:
                self._binc("macro_aligned_or_neutral_local_acceptance")
            self._claim_episode(plan)
            output.append(plan)
            self._binc("local_continuation_plan_allowed")
            self._local_bundle_trace.append(
                {
                    "scenario_kind": "local_auction_continuation_plan_allowed",
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
                        LOCAL_AUCTION_CONTINUATION_RULE,
                        LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
                        COMMON_FACTOR_VETO_ONLY_RULE,
                        SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
                        MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE,
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
            factor = self._market_factor_state
            if factor is not None and factor.side is not plan.side:
                self._hinc("horizontal_flip_rejected_by_opposing_common_factor")
                continue
            macro_side = getattr(self, "_macro_side", None)
            if macro_side is not None and macro_side is not plan.side:
                self._hinc("counter_macro_horizontal_acceptance_owned_by_flip")
            else:
                self._hinc("macro_aligned_or_neutral_horizontal_acceptance")
            self._claim_episode(plan)
            output.append(plan)
            self._hinc("horizontal_flip_plan_allowed")
            self._horizontal_trace.append(
                {
                    "scenario_kind": "horizontal_flip_response_plan_allowed",
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
                        HORIZONTAL_FLIP_RESPONSE_RULE,
                        HORIZONTAL_FLIP_SIGNIFICANT_OBJECTIVE_RULE,
                        COMMON_FACTOR_VETO_ONLY_RULE,
                        MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE,
                    ),
                }
            )
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["mechanism_specific_context_router"] = {
            "rejection_context": "ACCEPTANCE_CONFIRMED_60M_ROUTER",
            "local_continuation_context": "LOCAL_BOS_FLOW_OB_RESPONSE_WITH_OPPOSING_COMMON_FACTOR_VETO_ONLY",
            "horizontal_flip_context": "BREAK_HOLD_RETEST_RESPONSE_WITH_OPPOSING_COMMON_FACTOR_VETO_ONLY",
            "rules": (
                MACRO_BREAK_ACCEPTANCE_RULE,
                COMMON_FACTOR_VETO_ONLY_RULE,
                MECHANISM_SPECIFIC_CONTEXT_ROUTER_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AuctionRouterBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
