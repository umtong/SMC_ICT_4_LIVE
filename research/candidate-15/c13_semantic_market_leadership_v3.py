"""Cross-market auction roles for Candidate 13 v3.

The detector proposes FAR or AAC. This module assigns an economic role before
execution. Divided or late evidence must replace unanimity with a full-ATR
candidate event; all other scales are inherited from the frozen market gate.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision, MarketLeadershipGate

FAR_EXHAUSTION_UNANIMOUS = "SEMANTIC_FAR_EXHAUSTION_UNANIMOUS"
FAR_EXHAUSTION_QUORUM = "SEMANTIC_FAR_EXHAUSTION_DOMINANT_QUORUM_FULL_ATR"
FAR_EXHAUSTION_LAGGARD = "SEMANTIC_FAR_EXHAUSTION_LAGGARD_FULL_ATR"
FAR_CAPITULATION_SYNCHRONIZED = "SEMANTIC_FAR_CAPITULATION_SYNCHRONIZED"
FAR_CAPITULATION_IDIOSYNCRATIC = "SEMANTIC_FAR_CAPITULATION_IDIOSYNCRATIC"
FAR_NASCENT_TREND_RESUMPTION = "SEMANTIC_FAR_NASCENT_TREND_RESUMPTION"
FAR_ROTATION_UNANIMOUS = "SEMANTIC_FAR_ROTATION_TRANSFER_UNANIMOUS"
FAR_ROTATION_DISPLACEMENT = "SEMANTIC_FAR_ROTATION_TRANSFER_EVENT_DISPLACEMENT"
FAR_IDIOSYNCRATIC = "SEMANTIC_FAR_IDIOSYNCRATIC_PRICE_DISCOVERY"
AAC_ALIGNED = "SEMANTIC_AAC_ALIGNED_SYNCHRONIZED_NONLAGGARD"
AAC_LAGGARD_TRANSFER = "SEMANTIC_AAC_LAGGARD_TRANSFER"


def _with(d: LeadershipDecision, approved: bool, reason: str) -> LeadershipDecision:
    return replace(d, approved=approved, reason=reason)


def _peer_state(d: LeadershipDecision, sign: float) -> tuple[int, int, bool]:
    signed = [sign * float(v) for v in d.peer_returns.values()]
    aligned = [v for v in signed if v > 0.0]
    dissent = [-v for v in signed if v <= 0.0]
    required = len(signed) // 2 + 1
    dominant = len(aligned) >= required and (not dissent or max(dissent) < min(aligned))
    return len(aligned), len(dissent), dominant


def _quality(d: LeadershipDecision, efficiency: float, displacement: float) -> bool:
    return (
        d.event_path_efficiency is not None
        and d.event_path_efficiency >= efficiency
        and d.event_standardized_displacement is not None
        and d.event_standardized_displacement >= displacement
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
        and decision.trailing_direction_rank is not None
    )
    if not complete:
        return _with(decision, False, decision.reason)

    sign = 1.0 if decision.direction == "LONG" else -1.0
    aligned, dissent, dominant_quorum = _peer_state(decision, sign)
    unanimous = aligned == required_peers
    move = float(decision.candidate_event_move)
    impulse = float(decision.confirmation_impulse)
    event_rank = int(decision.event_direction_rank)
    trailing_rank = int(decision.trailing_direction_rank)
    scores = {s: float(v) for s, v in decision.directional_trend_scores.items()}
    candidate_trend = scores[decision.symbol]
    market_trend = float(median(scores.values()))
    all_prior_adverse = all(v < 0.0 for v in scores.values())
    severe = (
        candidate_trend <= severe_adverse_trend_score
        and market_trend <= severe_adverse_trend_score
    )
    event_quality = _quality(
        decision,
        minimum_event_efficiency,
        minimum_event_displacement,
    )
    full_atr_event = _quality(decision, minimum_event_efficiency, 1.0)

    if decision.scenario == "FAR":
        if move <= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_WITHOUT_LOCAL_RECLAIM")

        # Trend-direction sweep/reclaim is a separate transfer state. It is
        # tradable only while the candidate's completed trend is still nascent
        # and a fresh >=1.5 ATR impulse is transferred through two peers into
        # an intermediate non-liquidity-leader.
        if candidate_trend > 0.0 and market_trend > 0.0:
            nascent = (
                candidate_trend < minimum_event_displacement
                and aligned == required_peers - 1
                and dissent == 1
                and decision.symbol != decision.leader
                and 1 < trailing_rank < symbol_count
                and event_rank < symbol_count
                and impulse >= abs(severe_adverse_trend_score)
            )
            if nascent:
                return _with(decision, True, FAR_NASCENT_TREND_RESUMPTION)
            return _with(decision, False, "SEMANTIC_FAR_NOT_COUNTERTREND")
        if candidate_trend >= 0.0 or market_trend >= 0.0:
            return _with(decision, False, "SEMANTIC_FAR_NOT_COUNTERTREND")

        # Deep capitulation needs either synchronized peer reclaim or a
        # non-liquidity-leader which itself leads with a full-ATR event.
        if severe:
            if unanimous and event_rank < symbol_count and impulse >= minimum_confirmation_impulse:
                return _with(decision, True, FAR_CAPITULATION_SYNCHRONIZED)
            if (
                decision.symbol != decision.leader
                and event_rank == 1
                and impulse >= minimum_confirmation_impulse
                and full_atr_event
            ):
                return _with(decision, True, FAR_CAPITULATION_IDIOSYNCRATIC)
            return _with(decision, False, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")

        if all_prior_adverse:
            if unanimous:
                if impulse < minimum_confirmation_impulse:
                    return _with(decision, False, "SEMANTIC_FAR_EXHAUSTION_WEAK_LOCAL_DISPLACEMENT")
                if event_rank >= symbol_count:
                    if full_atr_event:
                        return _with(decision, True, FAR_EXHAUSTION_LAGGARD)
                    return _with(decision, False, "SEMANTIC_FAR_EVENT_LAGGARD")
                return _with(decision, True, FAR_EXHAUSTION_UNANIMOUS)
            if not dominant_quorum:
                return _with(decision, False, "SEMANTIC_FAR_EXHAUSTION_REQUIRES_PEER_QUORUM")
            if impulse < minimum_confirmation_impulse:
                return _with(decision, False, "SEMANTIC_FAR_EXHAUSTION_WEAK_LOCAL_DISPLACEMENT")
            if decision.symbol == decision.leader:
                return _with(decision, False, "SEMANTIC_FAR_QUORUM_CANNOT_USE_LIQUIDITY_LEADER")
            if event_rank > max(1, symbol_count // 2):
                return _with(decision, False, "SEMANTIC_FAR_QUORUM_REQUIRES_LOCAL_EVENT_LEAD")
            if not full_atr_event:
                return _with(decision, False, "SEMANTIC_FAR_QUORUM_REQUIRES_FULL_ATR_EVENT")
            return _with(decision, True, FAR_EXHAUSTION_QUORUM)

        # Split completed auctions require unanimous event transfer or an
        # independent first-mover; partial consensus remains unresolved.
        if unanimous:
            if event_rank >= symbol_count and not full_atr_event:
                return _with(decision, False, "SEMANTIC_FAR_EVENT_LAGGARD")
            if impulse >= minimum_confirmation_impulse:
                return _with(decision, True, FAR_ROTATION_UNANIMOUS)
            if event_quality:
                return _with(decision, True, FAR_ROTATION_DISPLACEMENT)
            return _with(decision, False, "SEMANTIC_FAR_ROTATION_REQUIRES_IMPULSE_OR_EVENT_QUALITY")
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
        if not unanimous:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_UNANIMOUS_PEER_ACCEPTANCE")
        if move <= 0.0:
            return _with(decision, False, "SEMANTIC_AAC_WITHOUT_LOCAL_ACCEPTANCE")
        if impulse < minimum_confirmation_impulse:
            return _with(decision, False, "SEMANTIC_AAC_WEAK_LOCAL_DISPLACEMENT")
        if not event_quality:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_LOCAL_EVENT_QUALITY")
        if candidate_trend <= 0.0 or market_trend <= 0.0:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_ALIGNED_TRAILING_AUCTION")
        if event_rank >= symbol_count:
            if decision.symbol != decision.leader and trailing_rank < symbol_count:
                return _with(decision, True, AAC_LAGGARD_TRANSFER)
            return _with(decision, False, "SEMANTIC_AAC_EVENT_LAGGARD")
        return _with(decision, True, AAC_ALIGNED)

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
