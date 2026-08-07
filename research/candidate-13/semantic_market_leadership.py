"""Semantic separation of failed auctions and accepted repricing.

Candidate 13 v1 reused a causal detector but allowed the leadership gate to
approve the same cross-market state as either FAR or continuation depending on
which local state machine happened to emit first.  The untouched W10-W14 run
showed that this semantic overlap was the dominant failure mode.

This module changes no detector, target, stop, fee, sizing, order or execution
parameter.  It reclassifies only the already-completed, synchronized evidence
returned by the frozen base gate:

* FAR is a moderate counter-trend failed auction.  The local market must reclaim
  in the proposed direction, all three peers must move with it after the sweep,
  and the preceding 24-hour auction must still be adverse but not severely
  unresolved.
* AAC is accepted repricing.  All peers must agree, while the candidate itself
  must rank first during the event and exhibit efficient, volatility-normalized
  displacement.  Quote-notional leadership is informative but not a monopoly:
  a follower that actually leads the event may carry price discovery.

Every input is visible at the confirmation close.  Missing or asynchronous
state continues to fail closed in the base implementation.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision, MarketLeadershipGate


def _with(decision: LeadershipDecision, approved: bool, reason: str) -> LeadershipDecision:
    return replace(decision, approved=approved, reason=reason)


def semantic_decision(
    decision: LeadershipDecision,
    *,
    symbol_count: int,
    severe_adverse_trend_score: float,
    minimum_confirmation_impulse: float,
    minimum_event_efficiency: float,
    minimum_event_displacement: float,
) -> LeadershipDecision:
    """Apply one causal semantic partition to a fully observed base decision."""
    required_peers = symbol_count - 1
    complete = (
        len(decision.peer_returns) == required_peers
        and len(decision.directional_trend_scores) == symbol_count
        and decision.candidate_event_move is not None
        and decision.confirmation_impulse is not None
        and decision.event_direction_rank is not None
    )
    if not complete:
        # Preserve the more precise causal precondition emitted by the base gate.
        return _with(decision, False, decision.reason)

    sign = 1.0 if decision.direction == "LONG" else -1.0
    signed_peer_moves = [sign * value for value in decision.peer_returns.values()]
    all_peers_aligned = all(value > 0.0 for value in signed_peer_moves)
    candidate_move = float(decision.candidate_event_move)
    impulse = float(decision.confirmation_impulse)
    candidate_trend = float(decision.directional_trend_scores[decision.symbol])
    market_trend = float(median(decision.directional_trend_scores.values()))

    if decision.scenario == "FAR":
        if not all_peers_aligned:
            return _with(decision, False, "SEMANTIC_FAR_REQUIRES_UNANIMOUS_PEER_RECLAIM")
        if candidate_move <= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_WITHOUT_LOCAL_RECLAIM")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")
        if decision.event_direction_rank >= symbol_count:
            return _with(decision, False, "SEMANTIC_FAR_EVENT_LAGGARD")
        # A failed auction is a reversal.  If the proposed direction already
        # owns the trailing auction, this is continuation and must wait for AAC.
        if candidate_trend >= 0.0 or market_trend >= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_NOT_COUNTERTREND")
        severe_unresolved = (
            candidate_trend <= severe_adverse_trend_score
            and market_trend <= severe_adverse_trend_score
        )
        if severe_unresolved:
            return _with(decision, False, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")
        return _with(decision, True, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")

    if decision.scenario == "AAC":
        if not all_peers_aligned:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_UNANIMOUS_PEER_ACCEPTANCE")
        if candidate_move <= 0.0:
            return _with(decision, False, "SEMANTIC_AAC_WITHOUT_LOCAL_ACCEPTANCE")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_AAC_WEAK_LOCAL_DISPLACEMENT")
        if decision.event_direction_rank != 1:
            return _with(decision, False, "SEMANTIC_AAC_CANDIDATE_NOT_EVENT_LEADER")
        if (
            decision.event_path_efficiency is None
            or decision.event_path_efficiency < minimum_event_efficiency
        ):
            return _with(decision, False, "SEMANTIC_AAC_INEFFICIENT_EVENT_PATH")
        if (
            decision.event_standardized_displacement is None
            or decision.event_standardized_displacement < minimum_event_displacement
        ):
            return _with(decision, False, "SEMANTIC_AAC_INSUFFICIENT_EVENT_DISPLACEMENT")
        severe_unresolved = (
            candidate_trend <= severe_adverse_trend_score
            and market_trend <= severe_adverse_trend_score
        )
        if severe_unresolved:
            return _with(decision, False, "SEMANTIC_AAC_UNRESOLVED_ADVERSE_AUCTION")
        return _with(decision, True, "SEMANTIC_AAC_SYNCHRONIZED_EVENT_LEADER")

    return _with(decision, False, "SEMANTIC_UNSUPPORTED_SCENARIO")


class SemanticMarketLeadershipGate(MarketLeadershipGate):
    """Base causal measurements with mutually exclusive FAR/AAC semantics."""

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
        return semantic_decision(
            measured,
            symbol_count=len(self.symbols),
            severe_adverse_trend_score=self.severe_adverse_trend_score,
            minimum_confirmation_impulse=self.minimum_follower_confirmation_impulse,
            minimum_event_efficiency=self.minimum_idiosyncratic_event_efficiency,
            minimum_event_displacement=self.minimum_idiosyncratic_event_displacement,
        )
