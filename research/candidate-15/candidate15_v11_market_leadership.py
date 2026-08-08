"""Candidate 15 V11 leadership adapter.

Candidate 15 V11 keeps Candidate 13 V16 semantics for FAR/AAC core plans, but
Candidate 14's combined runner still calls ``decide_session`` before Candidate
15's explicit SESSION_I7 quarantine.  Replacing the gate with the V16 class
therefore removed an interface the combined runner requires and stopped the
Nautilus evaluation before strategy evidence could be produced.

This adapter preserves V16's ``decide`` policy and implements the legacy
session interface as an unconditional fail-closed decision.  It does not admit
SESSION_I7, change risk, or alter any V11 core/transfer decision.
"""
from __future__ import annotations

from dataclasses import replace

from market_leadership import LeadershipDecision
from c13_semantic_market_leadership_v16 import (
    SemanticMarketLeadershipGate as Candidate13V16SemanticMarketLeadershipGate,
)


SESSION_FAMILY_QUARANTINED = "C15_V11_SESSION_FAMILY_QUARANTINED"


class Candidate15V11SemanticMarketLeadershipGate(
    Candidate13V16SemanticMarketLeadershipGate,
):
    """V16 core semantics plus a fail-closed combined-runner session contract."""

    def decide_session(
        self,
        *,
        symbol: str,
        scenario: str,
        direction: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
    ) -> LeadershipDecision:
        measured = self.decide(
            symbol=symbol,
            scenario=scenario,
            direction=direction,
            sweep_ts_ns=sweep_ts_ns,
            confirmation_ts_ns=confirmation_ts_ns,
        )
        return replace(
            measured,
            approved=False,
            reason=SESSION_FAMILY_QUARANTINED,
        )
