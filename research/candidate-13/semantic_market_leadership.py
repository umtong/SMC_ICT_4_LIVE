"""Mutually exclusive cross-market auction states for Candidate 13.

FAR is a moderate counter-trend failed auction.  The strongest form requires
all three peers to reclaim in the proposed direction.  A second form admits one
sub-dominant dissenting peer only when the candidate is a follower, completes a
top-half local event, contributes an efficient volatility-normalized reclaim,
and the completed market-wide auction is coherently adverse to the proposed
reversal.  This treats tiny asynchronous peer noise differently from a material
market disagreement without weakening the local auction evidence.

AAC is synchronized accepted repricing in the direction already controlling
both the candidate's and the market's completed 24-hour auction.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision, MarketLeadershipGate


def _with(decision: LeadershipDecision, approved: bool, reason: str) -> LeadershipDecision:
    return replace(decision, approved=approved, reason=reason)


def _dominant_peer_quorum(decision: LeadershipDecision, sign: float) -> bool:
    """Return true when a strict peer majority dominates a lone dissent.

    Returns are already synchronized to the candidate sweep and confirmation.
    No free threshold is introduced: the absolute dissent must be smaller than
    every aligned peer move.  With the four-market universe this means two of
    three peers agree and the third is economically subordinate.
    """
    signed = [sign * float(value) for value in decision.peer_returns.values()]
    required = len(signed) // 2 + 1
    aligned = [value for value in signed if value > 0.0]
    dissent = [-value for value in signed if value <= 0.0]
    if len(aligned) < required:
        return False
    return not dissent or max(dissent) < min(aligned)


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
    dominant_quorum = _dominant_peer_quorum(decision, sign)
    candidate_move = float(decision.candidate_event_move)
    impulse = float(decision.confirmation_impulse)
    candidate_trend = float(decision.directional_trend_scores[decision.symbol])
    market_trend = float(median(decision.directional_trend_scores.values()))
    event_rank = int(decision.event_direction_rank)

    if decision.scenario == "FAR":
        if not all_peers_aligned and not dominant_quorum:
            return _with(decision, False, "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM")
        if candidate_move <= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_WITHOUT_LOCAL_RECLAIM")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")
        if event_rank >= symbol_count:
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

        if all_peers_aligned:
            return _with(decision, True, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")

        # Partial event consensus is usable only as information transfer into
        # a follower from a coherent market-wide prior auction.  If any market
        # was already trending in the proposed direction, the divided event is
        # more plausibly rotation than exhaustion of one common auction.
        if not all(float(value) < 0.0 for value in decision.directional_trend_scores.values()):
            return _with(
                decision,
                False,
                "SEMANTIC_FAR_QUORUM_REQUIRES_COHERENT_ADVERSE_AUCTION",
            )
        if decision.symbol == decision.leader:
            return _with(decision, False, "SEMANTIC_FAR_QUORUM_CANNOT_USE_LIQUIDITY_LEADER")
        if event_rank > max(1, symbol_count // 2):
            return _with(decision, False, "SEMANTIC_FAR_QUORUM_REQUIRES_LOCAL_EVENT_LEAD")
        if (
            decision.event_path_efficiency is None
            or decision.event_path_efficiency < minimum_event_efficiency
        ):
            return _with(decision, False, "SEMANTIC_FAR_QUORUM_INEFFICIENT_LOCAL_PATH")
        if (
            decision.event_standardized_displacement is None
            or decision.event_standardized_displacement < minimum_event_displacement
        ):
            return _with(decision, False, "SEMANTIC_FAR_QUORUM_INSUFFICIENT_LOCAL_DISPLACEMENT")
        return _with(decision, True, "SEMANTIC_FAR_DOMINANT_PEER_QUORUM")

    if decision.scenario == "AAC":
        if not all_peers_aligned:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_UNANIMOUS_PEER_ACCEPTANCE")
        if candidate_move <= 0.0:
            return _with(decision, False, "SEMANTIC_AAC_WITHOUT_LOCAL_ACCEPTANCE")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_AAC_WEAK_LOCAL_DISPLACEMENT")
        if event_rank >= symbol_count:
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
