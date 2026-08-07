"""Candidate 14 event-price-discovery transfer semantics.

The local SMC/ICT state machine already proves a causal liquidity episode:
completed range -> external liquidity trade-through -> reclaim or acceptance ->
local structure displacement -> independent external target.  This module does
not add another pattern.  It classifies how that completed local episode relates
to the synchronized four-market auction.

Candidate 13 treated three useful cases as failures: a sweep which resumes the
already controlling trend, a multi-bar recovery whose last one-minute return is
not exceptional, and a locally confirmed laggard after every peer has already
moved.  Candidate 14 makes those cases explicit and mutually exclusive:

* COUNTERTREND_REVERSAL: a moderate adverse auction exhausts and transfers.
* TREND_RESUMPTION: a counter-directional liquidity raid fails inside the
  already controlling auction.
* ORIGINATOR_TRANSFER: the candidate is the first efficient local price
  discovery event and at least one peer has begun to follow.
* LAGGARD_TRANSFER: all peers have already repriced and the candidate completes
  its own efficient structural confirmation with costed target room remaining.

No role changes position size.  Missing/asynchronous state, material unsupported
peer opposition, mixed trailing context and weak local paths fail closed.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median

from market_leadership import LeadershipDecision, MarketLeadershipGate


def _with(decision: LeadershipDecision, approved: bool, reason: str) -> LeadershipDecision:
    return replace(decision, approved=approved, reason=reason)


def _peer_transfer_state(
    decision: LeadershipDecision,
    sign: float,
) -> tuple[bool, bool, bool, float, float]:
    """Return all-aligned, common-transfer and originator-transfer evidence.

    Common transfer requires a strict peer-sign majority and greater directional
    peer energy than opposition.  Originator transfer permits the locally
    leading candidate to precede two peers, but only after one peer has followed
    and candidate-plus-following displacement dominates visible opposition.
    These are identities of the four-market observation set, not fitted cutoffs.
    """
    signed = [sign * float(value) for value in decision.peer_returns.values()]
    aligned = [value for value in signed if value > 0.0]
    opposed = [-value for value in signed if value <= 0.0]
    support = sum(aligned)
    opposition = sum(opposed)
    required = len(signed) // 2 + 1
    all_aligned = len(aligned) == len(signed)
    common_transfer = len(aligned) >= required and support > opposition
    candidate_move = float(decision.candidate_event_move or 0.0)
    originator_transfer = (
        int(decision.event_direction_rank or 0) == 1
        and bool(aligned)
        and candidate_move > 0.0
        and candidate_move + support > opposition
    )
    return all_aligned, common_transfer, originator_transfer, support, opposition


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
    candidate_move = float(decision.candidate_event_move)
    impulse = float(decision.confirmation_impulse)
    event_rank = int(decision.event_direction_rank)
    efficiency = decision.event_path_efficiency
    displacement = decision.event_standardized_displacement
    path_efficient = efficiency is not None and efficiency >= minimum_event_efficiency
    path_displaced = displacement is not None and displacement >= minimum_event_displacement
    path_confirmed = path_efficient and path_displaced
    impulse_confirmed = impulse >= minimum_confirmation_impulse

    candidate_trend = float(decision.directional_trend_scores[decision.symbol])
    market_trend = float(median(decision.directional_trend_scores.values()))
    all_aligned, common_transfer, originator_transfer, support, opposition = (
        _peer_transfer_state(decision, sign)
    )
    laggard = event_rank == symbol_count

    if candidate_move <= 0.0:
        return _with(decision, False, f"SEMANTIC_{decision.scenario}_WITHOUT_LOCAL_DIRECTIONAL_MOVE")

    if decision.scenario == "FAR":
        if not (impulse_confirmed or path_confirmed):
            return _with(decision, False, "SEMANTIC_FAR_WITHOUT_LOCAL_PATH_OR_IMPULSE")

        countertrend = candidate_trend < 0.0 and market_trend < 0.0
        trend_resumption = candidate_trend > 0.0 and market_trend > 0.0
        if countertrend and (
            candidate_trend <= severe_adverse_trend_score
            and market_trend <= severe_adverse_trend_score
        ):
            return _with(decision, False, "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION")
        if not countertrend and not trend_resumption:
            return _with(decision, False, "SEMANTIC_FAR_MIXED_TRAILING_AUCTION")

        role = "COUNTERTREND_REVERSAL" if countertrend else "TREND_RESUMPTION"

        if laggard:
            if not all_aligned:
                return _with(decision, False, "SEMANTIC_FAR_LAGGARD_WITHOUT_UNANIMOUS_TRANSFER")
            if not path_confirmed:
                return _with(decision, False, "SEMANTIC_FAR_LAGGARD_WITHOUT_EFFICIENT_LOCAL_PATH")
            return _with(decision, True, f"SEMANTIC_FAR_{role}_LAGGARD_TRANSFER")

        if not (common_transfer or originator_transfer):
            return _with(
                decision,
                False,
                "SEMANTIC_FAR_PEER_OPPOSITION_DOMINATES_TRANSFER",
            )

        if originator_transfer and not common_transfer:
            if not path_confirmed:
                return _with(decision, False, "SEMANTIC_FAR_ORIGINATOR_WITHOUT_EFFICIENT_LOCAL_PATH")
            return _with(decision, True, f"SEMANTIC_FAR_{role}_ORIGINATOR_TRANSFER")

        # A trend-resumption raid must demonstrate a coherent multi-bar path;
        # one terminal impulse alone is not evidence that the prior trend has
        # regained control after the liquidity event.
        if trend_resumption and not path_confirmed:
            return _with(decision, False, "SEMANTIC_FAR_TREND_RESUMPTION_WITHOUT_EFFICIENT_PATH")

        evidence = "PATH" if path_confirmed and not impulse_confirmed else "IMPULSE"
        return _with(decision, True, f"SEMANTIC_FAR_{role}_COMMON_{evidence}")

    if decision.scenario == "AAC":
        if candidate_trend <= 0.0 or market_trend <= 0.0:
            return _with(decision, False, "SEMANTIC_AAC_REQUIRES_ALIGNED_TRAILING_AUCTION")
        if not path_efficient:
            return _with(decision, False, "SEMANTIC_AAC_INEFFICIENT_EVENT_PATH")
        if not path_displaced:
            return _with(decision, False, "SEMANTIC_AAC_INSUFFICIENT_EVENT_DISPLACEMENT")

        if laggard:
            if not all_aligned:
                return _with(decision, False, "SEMANTIC_AAC_LAGGARD_WITHOUT_UNANIMOUS_TRANSFER")
            return _with(decision, True, "SEMANTIC_AAC_LAGGARD_TRANSFER")

        if not (common_transfer or originator_transfer):
            return _with(
                decision,
                False,
                "SEMANTIC_AAC_PEER_OPPOSITION_DOMINATES_TRANSFER",
            )
        if originator_transfer and not common_transfer:
            return _with(decision, True, "SEMANTIC_AAC_ORIGINATOR_TRANSFER")
        if not impulse_confirmed:
            return _with(decision, True, "SEMANTIC_AAC_COMMON_PATH_CONFIRMATION")
        return _with(decision, True, "SEMANTIC_AAC_COMMON_REPRICING")

    return _with(decision, False, "SEMANTIC_UNSUPPORTED_SCENARIO")


class EventPriceDiscoveryTransferGate(MarketLeadershipGate):
    """Binary event-local price-discovery approval; risk remains exactly 3%."""

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
