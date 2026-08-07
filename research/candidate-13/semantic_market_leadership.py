"""Mutually exclusive cross-market auction roles for Candidate 13.

The pattern engine detects a failed-auction reclaim.  This module decides which
economic role, if any, that reclaim is allowed to trade:

* EXHAUSTION: every market's completed 24-hour auction is adverse to the
  proposed reversal.  The event therefore needs a dominant peer reclaim; the
  strongest form is unanimous.
* ROTATION_TRANSFER: the completed auctions are already split, while the
  candidate and market median remain adverse.  A coordinated peer event can
  transfer the first rotation into the lagging market.  When the final
  confirmation impulse is modest, the candidate must replace it with an
  efficient volatility-normalized event path.
* IDIOSYNCRATIC_PRICE_DISCOVERY: completed auctions are split and peers do not
  form a dominant event quorum.  Only a non-liquidity-leader which is first in
  the event and contributes its own efficient displacement may lead.

AAC remains synchronized accepted repricing in the direction controlling both
the candidate's and market's completed auction.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision, MarketLeadershipGate


FAR_EXHAUSTION_UNANIMOUS = "SEMANTIC_FAR_EXHAUSTION_UNANIMOUS"
FAR_EXHAUSTION_QUORUM = "SEMANTIC_FAR_EXHAUSTION_DOMINANT_QUORUM"
FAR_ROTATION_UNANIMOUS = "SEMANTIC_FAR_ROTATION_TRANSFER_UNANIMOUS"
FAR_ROTATION_DISPLACEMENT = "SEMANTIC_FAR_ROTATION_TRANSFER_EVENT_DISPLACEMENT"
FAR_IDIOSYNCRATIC = "SEMANTIC_FAR_IDIOSYNCRATIC_PRICE_DISCOVERY"


def _with(decision: LeadershipDecision, approved: bool, reason: str) -> LeadershipDecision:
    return replace(decision, approved=approved, reason=reason)


def _dominant_peer_quorum(decision: LeadershipDecision, sign: float) -> bool:
    """Return true when a strict peer majority dominates a lone dissent.

    No fitted magnitude is introduced.  The absolute dissent must be smaller
    than every aligned peer move.  In the four-market universe this means two
    of three peers agree and the third is economically subordinate.
    """
    signed = [sign * float(value) for value in decision.peer_returns.values()]
    required = len(signed) // 2 + 1
    aligned = [value for value in signed if value > 0.0]
    dissent = [-value for value in signed if value <= 0.0]
    if len(aligned) < required:
        return False
    return not dissent or max(dissent) < min(aligned)


def _event_quality(
    decision: LeadershipDecision,
    *,
    minimum_event_efficiency: float,
    minimum_event_displacement: float,
) -> bool:
    return (
        decision.event_path_efficiency is not None
        and decision.event_path_efficiency >= minimum_event_efficiency
        and decision.event_standardized_displacement is not None
        and decision.event_standardized_displacement >= minimum_event_displacement
    )


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
    event_rank = int(decision.event_direction_rank)
    scores = {symbol: float(value) for symbol, value in decision.directional_trend_scores.items()}
    candidate_trend = scores[decision.symbol]
    market_trend = float(median(scores.values()))
    all_prior_adverse = all(value < 0.0 for value in scores.values())
    event_quality = _event_quality(
        decision,
        minimum_event_efficiency=minimum_event_efficiency,
        minimum_event_displacement=minimum_event_displacement,
    )

    if decision.scenario == "FAR":
        if candidate_move <= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_WITHOUT_LOCAL_RECLAIM")
        if event_rank >= symbol_count:
            return _with(decision, False, "SEMANTIC_FAR_EVENT_LAGGARD")
        if candidate_trend >= 0.0 or market_trend >= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_NOT_COUNTERTREND")
        if (
            candidate_trend <= severe_adverse_trend_score
            and market_trend <= severe_adverse_trend_score
        ):
            return _with(decision, False, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

        if all_prior_adverse:
            if not dominant_quorum:
                return _with(decision, False, "SEMANTIC_FAR_EXHAUSTION_REQUIRES_PEER_QUORUM")
            if impulse < minimum_confirmation_impulse:
                return _with(decision, False, "SEMANTIC_FAR_EXHAUSTION_WEAK_LOCAL_DISPLACEMENT")
            if all_peers_aligned:
                return _with(decision, True, FAR_EXHAUSTION_UNANIMOUS)
            if decision.symbol == decision.leader:
                return _with(decision, False, "SEMANTIC_FAR_QUORUM_CANNOT_USE_LIQUIDITY_LEADER")
            if event_rank > max(1, symbol_count // 2):
                return _with(decision, False, "SEMANTIC_FAR_QUORUM_REQUIRES_LOCAL_EVENT_LEAD")
            if not event_quality:
                return _with(decision, False, "SEMANTIC_FAR_QUORUM_REQUIRES_LOCAL_EVENT_QUALITY")
            return _with(decision, True, FAR_EXHAUSTION_QUORUM)

        # A divided completed auction is not exhaustion of one common trend.
        # It can trade only as synchronized rotation transfer or genuinely
        # idiosyncratic price discovery.
        if all_peers_aligned:
            if impulse >= minimum_confirmation_impulse:
                return _with(decision, True, FAR_ROTATION_UNANIMOUS)
            if event_quality:
                return _with(decision, True, FAR_ROTATION_DISPLACEMENT)
            return _with(decision, False, "SEMANTIC_FAR_ROTATION_REQUIRES_IMPULSE_OR_EVENT_QUALITY")

        # Partial consensus in a split prior auction is neither a common
        # exhaustion nor a synchronized transfer.
        if dominant_quorum:
            return _with(decision, False, "SEMANTIC_FAR_SPLIT_AUCTION_REQUIRES_UNANIMOUS_TRANSFER")

        if decision.symbol == decision.leader:
            return _with(decision, False, "SEMANTIC_FAR_IDIOSYNCRATIC_CANNOT_USE_LIQUIDITY_LEADER")
        if event_rank != 1:
            return _with(decision, False, "SEMANTIC_FAR_IDIOSYNCRATIC_REQUIRES_EVENT_LEAD")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_FAR_IDIOSYNCRATIC_WEAK_LOCAL_DISPLACEMENT")
        if not event_quality:
            return _with(decision, False, "SEMANTIC_FAR_IDIOSYNCRATIC_REQUIRES_LOCAL_EVENT_QUALITY")
        return _with(decision, True, FAR_IDIOSYNCRATIC)

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
