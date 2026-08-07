"""Candidate 14 cross-market semantics for SCDAM and completed sessions.

The ordinary SCDAM FAR/AAC policy is the preserved Candidate 13 core.  Session
I7 plans have already completed a route-specific five-minute rejection,
acceptance, MSS or FVG confirmation before this module is called.  Candidate 14
therefore uses a distinct FAR admission function for those completed session
plans: the final one-minute impulse is not required a second time, but the
entire causal path must be efficient, volatility-normalized, unanimously
supported by peers and in the top half of event price discovery.

A session failed auction may be either:

* counter-trend transfer after a completed session liquidity raid; or
* trend resumption after that raid fails against the already controlling
  synchronized auction.

Both roles use existing measurements and categorical ranks only.  No new fitted
threshold, route whitelist, risk multiplier or SCDAM relaxation is introduced.
Session AAC remains exactly the preserved SCDAM AAC semantic rule.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision, MarketLeadershipGate


def _with(decision: LeadershipDecision, approved: bool, reason: str) -> LeadershipDecision:
    return replace(decision, approved=approved, reason=reason)


def _complete(decision: LeadershipDecision, symbol_count: int) -> bool:
    return (
        len(decision.peer_returns) == symbol_count - 1
        and len(decision.directional_trend_scores) == symbol_count
        and decision.candidate_event_move is not None
        and decision.confirmation_impulse is not None
        and decision.trailing_direction_rank is not None
        and decision.event_direction_rank is not None
    )


def _dominant_peer_quorum(decision: LeadershipDecision, sign: float) -> bool:
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
    """Preserved Candidate 13 SCDAM semantic policy."""
    if not _complete(decision, symbol_count):
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


def session_semantic_decision(
    decision: LeadershipDecision,
    *,
    symbol_count: int,
    severe_adverse_trend_score: float,
    minimum_confirmation_impulse: float,
    minimum_event_efficiency: float,
    minimum_event_displacement: float,
) -> LeadershipDecision:
    """Evaluate an already-complete I7 session plan against four-market state.

    AAC is deliberately unchanged.  FAR first receives the unchanged core
    decision.  Only a core rejection may enter the I7-specific substitution,
    where the full route confirmation replaces the final one-minute impulse.
    """
    core = semantic_decision(
        decision,
        symbol_count=symbol_count,
        severe_adverse_trend_score=severe_adverse_trend_score,
        minimum_confirmation_impulse=minimum_confirmation_impulse,
        minimum_event_efficiency=minimum_event_efficiency,
        minimum_event_displacement=minimum_event_displacement,
    )
    if core.approved or decision.scenario != "FAR":
        return core
    if not _complete(decision, symbol_count):
        return core

    sign = 1.0 if decision.direction == "LONG" else -1.0
    if not all(sign * float(value) > 0.0 for value in decision.peer_returns.values()):
        return _with(decision, False, "SEMANTIC_FAR_I7_REQUIRES_UNANIMOUS_PEER_TRANSFER")
    if float(decision.candidate_event_move or 0.0) <= 0.0:
        return _with(decision, False, "SEMANTIC_FAR_I7_WITHOUT_LOCAL_DIRECTIONAL_MOVE")
    if (
        decision.event_path_efficiency is None
        or decision.event_path_efficiency < minimum_event_efficiency
    ):
        return _with(decision, False, "SEMANTIC_FAR_I7_INEFFICIENT_CAUSAL_PATH")
    if (
        decision.event_standardized_displacement is None
        or decision.event_standardized_displacement < minimum_event_displacement
    ):
        return _with(decision, False, "SEMANTIC_FAR_I7_INSUFFICIENT_CAUSAL_DISPLACEMENT")

    top_half = max(1, (symbol_count + 1) // 2)
    event_rank = int(decision.event_direction_rank)
    trailing_rank = int(decision.trailing_direction_rank)
    if event_rank > top_half:
        return _with(decision, False, "SEMANTIC_FAR_I7_EVENT_NOT_TOP_HALF")

    candidate_trend = float(decision.directional_trend_scores[decision.symbol])
    market_trend = float(median(decision.directional_trend_scores.values()))
    countertrend = candidate_trend < 0.0 and market_trend < 0.0
    trend_resumption = candidate_trend > 0.0 and market_trend > 0.0

    if countertrend:
        if (
            candidate_trend <= severe_adverse_trend_score
            and market_trend <= severe_adverse_trend_score
        ):
            return _with(decision, False, "SEMANTIC_FAR_I7_UNRESOLVED_ADVERSE_AUCTION")
        return _with(decision, True, "SEMANTIC_FAR_I7_COUNTERTREND_TRANSFER")

    if trend_resumption:
        if trailing_rank > top_half:
            return _with(decision, False, "SEMANTIC_FAR_I7_TREND_RESUMPTION_NOT_TOP_HALF")
        return _with(decision, True, "SEMANTIC_FAR_I7_TREND_RESUMPTION")

    return _with(decision, False, "SEMANTIC_FAR_I7_MIXED_TRAILING_AUCTION")


class SemanticMarketLeadershipGate(MarketLeadershipGate):
    def _measure(
        self,
        *,
        symbol: str,
        scenario: str,
        direction: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
    ) -> LeadershipDecision:
        return super().decide(
            symbol=symbol,
            scenario=scenario,
            direction=direction,
            sweep_ts_ns=sweep_ts_ns,
            confirmation_ts_ns=confirmation_ts_ns,
        )

    def decide(
        self,
        *,
        symbol: str,
        scenario: str,
        direction: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
    ) -> LeadershipDecision:
        measured = self._measure(
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

    def decide_session(
        self,
        *,
        symbol: str,
        scenario: str,
        direction: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
    ) -> LeadershipDecision:
        measured = self._measure(
            symbol=symbol,
            scenario=scenario,
            direction=direction,
            sweep_ts_ns=sweep_ts_ns,
            confirmation_ts_ns=confirmation_ts_ns,
        )
        return session_semantic_decision(
            measured,
            symbol_count=len(self.symbols),
            severe_adverse_trend_score=self.severe_adverse_trend_score,
            minimum_confirmation_impulse=self.minimum_follower_confirmation_impulse,
            minimum_event_efficiency=self.minimum_idiosyncratic_event_efficiency,
            minimum_event_displacement=self.minimum_idiosyncratic_event_displacement,
        )
