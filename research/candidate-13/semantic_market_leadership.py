"""Mutually exclusive cross-market auction states for Candidate 13.

The same local sweep/reclaim detector can represent three different economic
states.  They are classified here instead of weakening the failed-auction rule:

* FAR: a moderate counter-trend failed auction with unanimous peer reclaim.
* LRC: a liquidity reclaim by the instrument that leads both the completed
  trailing auction and the confirmation event.
* LDT: a counter-trend directional transfer where all peers move first and the
  candidate is the last market to complete its own reclaim/displacement.

AAC remains accepted repricing aligned with both the candidate's and the
cross-market completed trailing auction.  Missing/asynchronous evidence fails
closed in the inherited measurement gate.
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
        and decision.trailing_direction_rank is not None
    )
    if not complete:
        return _with(decision, False, decision.reason)

    sign = 1.0 if decision.direction == "LONG" else -1.0
    all_peers_aligned = all(sign * value > 0.0 for value in decision.peer_returns.values())
    candidate_move = float(decision.candidate_event_move)
    impulse = float(decision.confirmation_impulse)
    candidate_trend = float(decision.directional_trend_scores[decision.symbol])
    market_trend = float(median(decision.directional_trend_scores.values()))
    event_rank = int(decision.event_direction_rank)
    trailing_rank = int(decision.trailing_direction_rank)

    if decision.scenario == "FAR":
        if not all_peers_aligned:
            return _with(decision, False, "SEMANTIC_FAR_REQUIRES_UNANIMOUS_PEER_RECLAIM")
        if candidate_move <= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_WITHOUT_LOCAL_RECLAIM")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")

        aligned_trailing = candidate_trend > 0.0 and market_trend > 0.0
        adverse_trailing = candidate_trend < 0.0 and market_trend < 0.0

        # LRC is not a failed auction.  The candidate itself must be first in
        # both completed-auction direction and confirmation-event displacement.
        if aligned_trailing:
            if trailing_rank == 1 and event_rank == 1:
                return _with(decision, True, "SEMANTIC_LRC_PERSISTENT_EVENT_LEADER")
            return _with(decision, False, "SEMANTIC_RECLAIM_ALIGNED_BUT_NOT_LEADER")

        if not adverse_trailing:
            return _with(decision, False, "SEMANTIC_FAR_MIXED_TRAILING_AUCTION")
        if candidate_trend <= severe_adverse_trend_score and market_trend <= severe_adverse_trend_score:
            return _with(decision, False, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

        # LDT is deliberately the opposite of local event leadership: all three
        # peers have already moved in the proposed direction and the candidate
        # is last to complete its own confirmed reclaim.  The candidate still
        # needs a volatility-normalized event displacement; path efficiency is
        # not required because lag is the mechanism being traded.
        if event_rank == symbol_count:
            if (
                decision.event_standardized_displacement is None
                or decision.event_standardized_displacement < minimum_event_displacement
            ):
                return _with(decision, False, "SEMANTIC_LDT_INSUFFICIENT_LOCAL_DISPLACEMENT")
            return _with(decision, True, "SEMANTIC_LDT_UNANIMOUS_PEER_LEAD")

        return _with(decision, True, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")

    if decision.scenario == "AAC":
        if not all_peers_aligned:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_UNANIMOUS_PEER_ACCEPTANCE")
        if candidate_move <= 0.0:
            return _with(decision, False, "SEMANTIC_AAC_WITHOUT_LOCAL_ACCEPTANCE")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_AAC_WEAK_LOCAL_DISPLACEMENT")
        if event_rank >= symbol_count:
            return _with(decision, False, "SEMANTIC_AAC_EVENT_LAGGARD")
        if decision.event_path_efficiency is None or decision.event_path_efficiency < minimum_event_efficiency:
            return _with(decision, False, "SEMANTIC_AAC_INEFFICIENT_EVENT_PATH")
        if (
            decision.event_standardized_displacement is None
            or decision.event_standardized_displacement < minimum_event_displacement
        ):
            return _with(decision, False, "SEMANTIC_AAC_INSUFFICIENT_EVENT_DISPLACEMENT")
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
