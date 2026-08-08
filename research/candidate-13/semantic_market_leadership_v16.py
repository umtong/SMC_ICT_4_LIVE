"""Candidate 13 V16 cross-market rotation-transfer role refinement.

V15 repaired FAR execution geometry.  The remaining exposed loss was labelled
``SEMANTIC_FAR_ROTATION_TRANSFER_EVENT_DISPLACEMENT`` even though the candidate
was the first event mover.  A first mover is the source of an idiosyncratic or
market-wide event, not a recipient of cross-market transfer.

V16 therefore changes one semantic decision only: an approved displacement-
quality rotation transfer must have ``event_direction_rank > 1``.  Rank-one
candidates become UNRESOLVED.  Every other FAR/AAC role and every execution,
risk, target and order rule is inherited unchanged from V15/V4.
"""
from __future__ import annotations

from dataclasses import replace

from market_leadership import LeadershipDecision
from semantic_market_leadership import FAR_ROTATION_DISPLACEMENT
from semantic_market_leadership_v4 import (
    SemanticMarketLeadershipGate as V4SemanticMarketLeadershipGate,
)


FAR_ROTATION_SOURCE_NOT_TRANSFER = "SEMANTIC_FAR_ROTATION_SOURCE_NOT_TRANSFER"


def refine_v15_decision(decision: LeadershipDecision) -> LeadershipDecision:
    """Reject a rank-one event source mislabelled as rotation transfer."""
    if (
        decision.approved
        and decision.scenario == "FAR"
        and decision.reason == FAR_ROTATION_DISPLACEMENT
        and decision.event_direction_rank is not None
        and int(decision.event_direction_rank) <= 1
    ):
        return replace(
            decision,
            approved=False,
            reason=FAR_ROTATION_SOURCE_NOT_TRANSFER,
        )
    return decision


class SemanticMarketLeadershipGate(V4SemanticMarketLeadershipGate):
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
        return refine_v15_decision(measured)
