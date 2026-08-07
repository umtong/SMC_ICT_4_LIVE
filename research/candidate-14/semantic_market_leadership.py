"""Candidate 14 development-v2 cross-market auction semantics.

Candidate 13's audited semantic decision is the immutable core.  Candidate 14
adds exactly one non-overlapping state: liquidity-leader catch-up.

A dynamic quote-notional leader may lag a proposed reversal while a strict
majority of peers has already completed the move.  If all peers then move in the
same direction during the local sweep-to-confirmation event, the leader itself
prints an efficient volatility-normalized recovery, and it is not the final
event laggard, the leader has transferred peer price discovery into the deepest
liquidity venue.  A strong multi-bar path may serve as confirmation even when
the last one-minute bar is not exceptional.

No trend-resumption, generic originator, generic laggard, quorum relaxation or
risk scaling is permitted.  Every other decision is byte-for-byte equivalent in
meaning to Candidate 13's core policy.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision, MarketLeadershipGate


def _with(decision: LeadershipDecision, approved: bool, reason: str) -> LeadershipDecision:
    return replace(decision, approved=approved, reason=reason)


def _dominant_peer_quorum(decision: LeadershipDecision, sign: float) -> bool:
    signed = [sign * float(value) for value in decision.peer_returns.values()]
    required = len(signed) // 2 + 1
    aligned = [value for value in signed if value > 0.0]
    dissent = [-value for value in signed if value <= 0.0]
    if len(aligned) < required:
        return False
    return not dissent or max(dissent) < min(aligned)


def _leader_catchup(
    decision: LeadershipDecision,
    *,
    symbol_count: int,
    severe_adverse_trend_score: float,
    minimum_event_efficiency: float,
    minimum_event_displacement: float,
) -> bool:
    """Return true only for leader catch-up into prior peer price discovery.

    The strict peer event unanimity is measured from the candidate sweep to its
    confirmation.  Prior peer leadership is a count identity: more than half of
    the *other* markets already have positive direction-signed 24-hour drift,
    while the dynamic liquidity leader itself still has negative drift.  No
    tunable magnitude threshold is added.
    """
    if decision.symbol != decision.leader:
        return False
    if decision.candidate_event_move is None or decision.candidate_event_move <= 0.0:
        return False
    if decision.event_direction_rank is None or decision.event_direction_rank >= symbol_count:
        return False
    if (
        decision.event_path_efficiency is None
        or decision.event_path_efficiency < minimum_event_efficiency
        or decision.event_standardized_displacement is None
        or decision.event_standardized_displacement < minimum_event_displacement
    ):
        return False

    sign = 1.0 if decision.direction == "LONG" else -1.0
    if not all(sign * float(value) > 0.0 for value in decision.peer_returns.values()):
        return False

    scores = decision.directional_trend_scores
    candidate_trend = float(scores[decision.symbol])
    market_trend = float(median(scores.values()))
    if candidate_trend >= 0.0 or market_trend >= 0.0:
        return False
    if (
        candidate_trend <= severe_adverse_trend_score
        and market_trend <= severe_adverse_trend_score
    ):
        return False

    peer_scores = [float(value) for symbol, value in scores.items() if symbol != decision.symbol]
    required_peer_leaders = len(peer_scores) // 2 + 1
    if sum(value > 0.0 for value in peer_scores) < required_peer_leaders:
        return False

    # The liquidity leader must truly be in the lagging half before the event,
    # otherwise this is ordinary core confirmation rather than catch-up.
    if (
        decision.trailing_direction_rank is None
        or decision.trailing_direction_rank <= symbol_count // 2
    ):
        return False
    return True


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
    all_peers_aligned = all(sign * value > 0.0 for value in decision.peer_returns.values())
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
            if _leader_catchup(
                decision,
                symbol_count=symbol_count,
                severe_adverse_trend_score=severe_adverse_trend_score,
                minimum_event_efficiency=minimum_event_efficiency,
                minimum_event_displacement=minimum_event_displacement,
            ):
                return _with(decision, True, "SEMANTIC_FAR_LIQUIDITY_LEADER_CATCHUP")
            return _with(decision, False, "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")

        if event_rank >= symbol_count:
            return _with(decision, False, "SEMANTIC_FAR_EVENT_LAGGARD")
        if candidate_trend >= 0.0 or market_trend >= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_NOT_COUNTERTREND")
        if (
            candidate_trend <= severe_adverse_trend_score
            and market_trend <= severe_adverse_trend_score
        ):
            return _with(decision, False, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

        if all_peers_aligned:
            return _with(decision, True, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")

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


class CorePlusLeaderCatchupGate(MarketLeadershipGate):
    """Candidate 13 core plus one binary leader-catch-up state."""

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


# Preserve the runner import boundary used by the first development iteration.
EventPriceDiscoveryTransferGate = CorePlusLeaderCatchupGate
