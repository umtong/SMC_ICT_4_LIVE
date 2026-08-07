"""Candidate 13 v4 auction-role refinement.

V3 separated causal FAR roles and AAC laggard transfer.  V4 leaves every FAR
rule unchanged and narrows ordinary AAC to the *early accepted repricing*
state.  A completed direction-signed auction score equal to the existing local
displacement floor means that repricing has already travelled one full event
unit; entering it as ordinary continuation is then a chase, not acceptance.

A true laggard transfer remains separate: the candidate may complete last in
the event only when it was not also the 24-hour directional laggard.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision
from semantic_market_leadership import (
    AAC_ALIGNED,
    AAC_LAGGARD_TRANSFER,
    SemanticMarketLeadershipGate as V3SemanticMarketLeadershipGate,
)


AAC_EARLY_REPRICING = "SEMANTIC_AAC_EARLY_ACCEPTED_REPRICING"


def refine_v3_decision(
    decision: LeadershipDecision,
    *,
    symbol_count: int,
    completed_auction_unit: float,
) -> LeadershipDecision:
    if not decision.approved or decision.scenario != "AAC":
        return decision

    if decision.reason == AAC_LAGGARD_TRANSFER:
        return decision
    if decision.reason != AAC_ALIGNED:
        return replace(decision, approved=False, reason="SEMANTIC_AAC_UNSUPPORTED_V4_ROLE")

    if decision.trailing_direction_rank is None:
        return replace(decision, approved=False, reason="SEMANTIC_AAC_MISSING_TRAILING_RANK")
    if int(decision.trailing_direction_rank) >= symbol_count:
        return replace(decision, approved=False, reason="SEMANTIC_AAC_TRAILING_LAGGARD")

    scores = decision.directional_trend_scores
    if decision.symbol not in scores or len(scores) != symbol_count:
        return replace(decision, approved=False, reason="SEMANTIC_AAC_INCOMPLETE_AUCTION_STATE")
    candidate_trend = float(scores[decision.symbol])
    market_trend = float(median(float(value) for value in scores.values()))
    if candidate_trend >= completed_auction_unit or market_trend >= completed_auction_unit:
        return replace(decision, approved=False, reason="SEMANTIC_AAC_COMPLETED_AUCTION_ALREADY_EXTENDED")
    return replace(decision, reason=AAC_EARLY_REPRICING)


class SemanticMarketLeadershipGate(V3SemanticMarketLeadershipGate):
    def decide(
        self,
        *,
        symbol: str,
        scenario: str,
        direction: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
    ) -> LeadershipDecision:
        measured = super().decide(
            symbol=symbol,
            scenario=scenario,
            direction=direction,
            sweep_ts_ns=sweep_ts_ns,
            confirmation_ts_ns=confirmation_ts_ns,
        )
        return refine_v3_decision(
            measured,
            symbol_count=len(self.symbols),
            completed_auction_unit=self.minimum_idiosyncratic_event_displacement,
        )
