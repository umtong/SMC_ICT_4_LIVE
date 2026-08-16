"""Flow-confirmed first response for the local-auction continuation family.

Formation flow proves that the five-minute order block was born from real
initiative.  It does not prove that the first return has transferred control
back to that initiative.  The first response close therefore needs one of two
observable mechanisms on its own completed minute:

* aligned active taker flow with material intended price progress; or
* active adverse taker flow which fails to prevent intended price progress
  beyond the first-touch extreme (absorption).

The first response is consumed even when neither mechanism appears.  The engine
never waits for a later convenient candle.  All structure, target, stop, account
and risk responsibilities remain unchanged from the local-auction candidate.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_re1_local_auction_continuation import (
    COMMON_FACTOR_VETO_ONLY_RULE,
    LOCAL_AUCTION_CONTINUATION_RULE,
    SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
    EasyChartRE1LocalAuctionContinuationBundle,
    EasyChartRE1LocalAuctionStrategy,
    LocalAuctionContinuationEngine,
)


LOCAL_CONTINUATION_RESPONSE_FLOW_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "FIRST_RETURN_CONTINUATION_ENTERS_ONLY_WHEN_ITS_FIRST_COMPLETED_RESPONSE_SHOWS_ALIGNED_INITIATIVE_OR_ADVERSE_FLOW_ABSORPTION"
)
if LOCAL_CONTINUATION_RESPONSE_FLOW_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,)


class FlowResponseLocalContinuationEngine(LocalAuctionContinuationEngine):
    """Require causal flow transfer on the already-required first response."""

    def _response_mechanism(self, setup: Any, bar: Candle) -> tuple[bool, str, Any]:
        observation = self.flow_analyzer.last_observation
        if observation is None or observation.ts_close_ns != bar.ts_close_ns:
            return False, "MISSING_CURRENT_RESPONSE_FLOW", observation
        if not observation.active or not observation.directed:
            return False, "RESPONSE_FLOW_NOT_ACTIVE_DIRECTED", observation

        aligned_delta = (
            observation.signed_taker_quote > 0.0
            if setup.side is Side.LONG
            else observation.signed_taker_quote < 0.0
        )
        opposite_delta = (
            observation.signed_taker_quote < 0.0
            if setup.side is Side.LONG
            else observation.signed_taker_quote > 0.0
        )
        intended_body = (
            observation.body > 0.0
            if setup.side is Side.LONG
            else observation.body < 0.0
        )
        aligned_initiative = (
            aligned_delta
            and intended_body
            and observation.material_progress
        )
        adverse_absorption = opposite_delta and intended_body
        if aligned_initiative:
            return True, "FIRST_RESPONSE_ALIGNED_INITIATIVE", observation
        if adverse_absorption:
            return True, "FIRST_RESPONSE_ADVERSE_FLOW_ABSORBED", observation
        return False, "FIRST_RESPONSE_FLOW_DID_NOT_TRANSFER_CONTROL", observation

    def _make_plan(self, setup: Any, bar: Candle) -> V5TradePlan | None:
        allowed, mechanism, observation = self._response_mechanism(setup, bar)
        if not allowed:
            self._linc("first_response_without_flow_transfer")
            self._finish(
                setup,
                "local_continuation_first_response_without_flow_transfer",
                bar.ts_close_ns,
                mechanism=mechanism,
                signed_taker_quote=None if observation is None else observation.signed_taker_quote,
                activity_ratio=None if observation is None else observation.activity_ratio,
                delta_ratio=None if observation is None else observation.delta_ratio,
                body_ratio=None if observation is None else observation.body_ratio,
                rule_provenance=LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
            )
            return None

        plan = super()._make_plan(setup, bar)
        if plan is None:
            return None
        self._linc(
            "first_response_aligned_initiative"
            if mechanism == "FIRST_RESPONSE_ALIGNED_INITIATIVE"
            else "first_response_adverse_flow_absorbed"
        )
        self._trace(
            "local_continuation_response_flow_confirmed",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            mechanism=mechanism,
            signed_taker_quote=observation.signed_taker_quote,
            activity_ratio=observation.activity_ratio,
            delta_ratio=observation.delta_ratio,
            body_ratio=observation.body_ratio,
            plan_id=plan.plan_id,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            gross_rr=plan.gross_rr,
            rule_provenance=LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
        )
        return plan


class EasyChartRE1LocalAuctionContinuationV2Bundle(
    EasyChartRE1LocalAuctionContinuationBundle,
):
    """Response-confirmed rejection plus flow-confirmed local continuation."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.local_continuation = FlowResponseLocalContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["local_continuation"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["local_auction_continuation_v2"] = {
            "first_response_flow": LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
            "rules": (
                LOCAL_AUCTION_CONTINUATION_RULE,
                COMMON_FACTOR_VETO_ONLY_RULE,
                SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
                LOCAL_CONTINUATION_RESPONSE_FLOW_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1LocalAuctionContinuationV2Bundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
