"""Mutually exclusive cross-market semantics for Candidate 13.

FAR is a moderate counter-trend failed auction with unanimous peer reclaim.
AAC is synchronized accepted repricing in the direction already controlling
both the candidate's and the market's completed 24-hour auction.  This makes
the two states mutually exclusive instead of allowing a counter-trend break to
be called continuation merely because peers moved during the confirmation
window.
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
    required_peers = symbol_count - 1
    complete = (
        len(decision.peer_returns) == required_peers
        and len(decision.directional_trend_scores) == symbol_count
        and decision.candidate_event_move is not None
        and decision.confirmation_impulse is not None
        and decision.event_direction_rank is not None
    )
    if not complete:
        return _with(decision, False, decision.reason)

    sign = 1.0 if decision.direction == "LONG" else -1.0
    all_peers_aligned = all(
        sign * value > 0.0 for value in decision.peer_returns.values()
    )
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
        # FAR is a reversal of the completed auction, not continuation with a
        # different local detector label.
        if candidate_trend >= 0.0 or market_trend >= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_NOT_COUNTERTREND")
        if (
            candidate_trend <= severe_adverse_trend_score
            and market_trend <= severe_adverse_trend_score
        ):
            return _with(decision, False, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")
        return _with(decision, True, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")

    if decision.scenario == "AAC":
        if not all_peers_aligned:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_UNANIMOUS_PEER_ACCEPTANCE")
        if candidate_move <= 0.0:
            return _with(decision, False, "SEMANTIC_AAC_WITHOUT_LOCAL_ACCEPTANCE")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_AAC_WEAK_LOCAL_DISPLACEMENT")
        if decision.event_direction_rank >= symbol_count:
            return _with(decision, False, "SEMANTIC_AAC_EVENT_LAGGARD")
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
        # Acceptance continuation must agree with both the candidate's and the
        # cross-market completed trailing auction.  Counter-trend acceptance is
        # a separate, unproven hypothesis and fails closed.
        if candidate_trend <= 0.0 or market_trend <= 0.0:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_ALIGNED_TRAILING_AUCTION")
        return _with(decision, True, "SEMANTIC_AAC_ALIGNED_SYNCHRONIZED_NONLAGGARD")

    return _with(decision, False, "SEMANTIC_UNSUPPORTED_SCENARIO")


class SemanticMarketLeadershipGate(MarketLeadershipGate):
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
