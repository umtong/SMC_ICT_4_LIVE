"""Cross-market event ownership for Candidate 14 V6.

Candidate 14 V5 allowed a second or third mover to borrow a synchronized peer
move and call it marketwide transfer. Frozen holdouts showed event-direction
rank two collapsing to one win in eight trades with profit factor 0.216. V6
therefore changes the economic category rather than fitting a return threshold:
only the market which leads the completed causal event can transfer global
initiative.

FAR retains the preserved SCDAM reclaim, impulse, adverse-auction and peer
coherence requirements, then requires event-direction rank one. AAC additionally
requires the quote-notional liquidity leader itself to own event rank one. The
rule uses categorical ownership already measured by the frozen gate; it adds no
magnitude, PnL, symbol whitelist, risk multiplier, or execution change.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision
from semantic_market_leadership import SemanticMarketLeadershipGate


def _reject(decision: LeadershipDecision, reason: str) -> LeadershipDecision:
    return replace(decision, approved=False, reason=reason)


def _approve(decision: LeadershipDecision, reason: str) -> LeadershipDecision:
    return replace(decision, approved=True, reason=reason)


class OwnershipMarketLeadershipGate(SemanticMarketLeadershipGate):
    """Admit only a locally completed scenario which owns price discovery."""

    def decide(
        self,
        *,
        symbol: str,
        scenario: str,
        direction: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
    ) -> LeadershipDecision:
        decision = super().decide(
            symbol=symbol,
            scenario=scenario,
            direction=direction,
            sweep_ts_ns=sweep_ts_ns,
            confirmation_ts_ns=confirmation_ts_ns,
        )
        if not decision.approved:
            return decision
        if decision.event_direction_rank != 1:
            return _reject(decision, f"V6_{scenario}_REQUIRES_EVENT_DIRECTION_OWNER")

        # candidate_event_move is already direction-signed by the frozen
        # measurement gate. Peer returns remain raw and are signed here once.
        sign = 1.0 if direction == "LONG" else -1.0
        candidate_move = float(decision.candidate_event_move or 0.0)
        peer_signed = [sign * float(value) for value in decision.peer_returns.values()]
        peer_median = median(peer_signed) if peer_signed else float("-inf")
        if candidate_move <= 0.0 or candidate_move < peer_median:
            return _reject(decision, f"V6_{scenario}_CANDIDATE_DOES_NOT_OWN_TRANSFER")

        if scenario == "FAR":
            return _approve(decision, "V6_FAR_EVENT_OWNER_CONFIRMS_TRANSFER")
        if scenario == "AAC":
            if symbol != decision.leader:
                return _reject(decision, "V6_AAC_REQUIRES_LIQUIDITY_AND_EVENT_OWNER")
            return _approve(decision, "V6_AAC_LIQUIDITY_EVENT_OWNER_ACCEPTANCE")
        return _reject(decision, "V6_UNSUPPORTED_SCENARIO")
