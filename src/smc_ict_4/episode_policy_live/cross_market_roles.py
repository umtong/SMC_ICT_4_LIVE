"""Causal cross-market auction roles, without a trade-admission gate.

This module is a narrow production port of mechanisms which already existed in
the research history.  It is not a newly discovered ``Missing Piece``:

* Candidate-13 frozen commit
  ``6e8e7a1461cbc77b82fa9f7f2ec2e29a37726b59``,
  ``research/candidate-13/market_leadership.py`` measured event-time direction
  rank, trailing-auction rank, synchronized peer participation and local path
  efficiency;
* the same commit's ``semantic_market_leadership.py`` separated unanimous,
  dominant-quorum and idiosyncratic price-discovery roles;
* the same commit's ``semantic_market_leadership_v4.py`` distinguished early
  accepted repricing, event-laggard transfer and an already extended auction;
* Candidate-02 result commit
  ``0330f2a04006e0b697899ba6c68010a6475aef1f`` recorded the V158 snapshot at
  ``research/candidate-02/results/v158_candidate13_v4_oi_router/``
  ``1b150d641740b6a3729740932f342959d2dce467/``.  Its
  strategy source was frozen at the Candidate-13 commit above.  The result was
  explicitly exposed-development evidence: 15 closed trades in 140 calendar
  days and only 9 active weeks.  Its perfect in-sample win count is therefore
  not copied as a claim, threshold or objective here.

The research implementation combined measurement with binary approval and a
bundle of tuned floors.  This port keeps only causal measurements and semantic
roles.  It never approves a trade.  Missing synchronized evidence is ``None``
or an ``UNKNOWN`` enum, never a neutral-looking zero.

Trailing direction is supplied by :mod:`directional_context`; this module does
not rebuild its multi-horizon direction model.  ``auction_progress_units`` is
also supplied by the caller in *completed-auction units*: one means one
structurally completed directional auction.  This preserves V4's early versus
extended meaning without importing its numeric displacement floor.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math
from statistics import median
from typing import Mapping, Sequence

from .directional_context import DirectionalContext


EPS = 1e-12


class PeerParticipation(StrEnum):
    """How peers participated in the proposed direction during the event."""

    UNKNOWN = "UNKNOWN"
    UNANIMOUS = "UNANIMOUS"
    DOMINANT_QUORUM = "DOMINANT_QUORUM"
    DIVIDED = "DIVIDED"
    NONE_ALIGNED = "NONE_ALIGNED"


class EventLeadershipRole(StrEnum):
    """The candidate's ordinal role in the event-local repricing."""

    UNKNOWN = "UNKNOWN"
    INDEPENDENT_LEAD = "INDEPENDENT_LEAD"
    UNANIMOUS_LEAD = "UNANIMOUS_LEAD"
    QUORUM_LEAD = "QUORUM_LEAD"
    CO_LEAD = "CO_LEAD"
    FOLLOWER = "FOLLOWER"
    LAGGARD = "LAGGARD"


class TrailingAuctionRole(StrEnum):
    """Ordinal role carried into the event by directional context."""

    UNKNOWN = "UNKNOWN"
    LEADER = "LEADER"
    FRONT_COHORT = "FRONT_COHORT"
    INTERMEDIATE = "INTERMEDIATE"
    LAGGARD = "LAGGARD"


class AcceptedRepricingPhase(StrEnum):
    """Acceptance state only; this is not a trade recommendation."""

    UNKNOWN = "UNKNOWN"
    NOT_ACCEPTED = "NOT_ACCEPTED"
    TRAILING_AUCTION_NOT_ALIGNED = "TRAILING_AUCTION_NOT_ALIGNED"
    EARLY_ACCEPTED_REPRICING = "EARLY_ACCEPTED_REPRICING"
    ALREADY_EXTENDED = "ALREADY_EXTENDED"
    LAGGARD_TRANSFER = "LAGGARD_TRANSFER"


class SourceOwnershipRole(StrEnum):
    """Who owns the completed directional delivery at a local source."""

    UNKNOWN = "UNKNOWN"
    NO_DIRECTIONAL_DELIVERY = "NO_DIRECTIONAL_DELIVERY"
    LOCAL_SOURCE_OWNER = "LOCAL_SOURCE_OWNER"
    COMMON_MARKET_OWNER_ONLY = "COMMON_MARKET_OWNER_ONLY"


@dataclass(frozen=True, slots=True)
class SourceOwnershipDecision:
    """Counterfactual ownership in pre-event volatility units.

    Zero is a semantic boundary, not a fitted threshold: a local source owns
    delivery only when the symbol moved in the proposed direction *and* moved
    farther than the synchronous peer-market component.
    """

    role: SourceOwnershipRole
    local_units: float | None
    common_units: float | None
    residual_units: float | None


def classify_source_ownership(
    *,
    local_units: float | None,
    peer_units: Sequence[float],
) -> SourceOwnershipDecision:
    """Separate local source control from a coincident common-market move."""

    if local_units is None or not math.isfinite(float(local_units)):
        return SourceOwnershipDecision(SourceOwnershipRole.UNKNOWN, None, None, None)
    local = float(local_units)
    if local <= 0.0:
        return SourceOwnershipDecision(
            SourceOwnershipRole.NO_DIRECTIONAL_DELIVERY,
            local,
            None,
            None,
        )
    peers = [float(value) for value in peer_units if math.isfinite(float(value))]
    if not peers:
        return SourceOwnershipDecision(SourceOwnershipRole.UNKNOWN, local, None, None)
    common = float(median(peers))
    residual = local - common
    role = (
        SourceOwnershipRole.LOCAL_SOURCE_OWNER
        if residual > 0.0
        else SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY
        if common > 0.0
        else SourceOwnershipRole.LOCAL_SOURCE_OWNER
    )
    return SourceOwnershipDecision(role, local, common, residual)


@dataclass(frozen=True, slots=True)
class EventPrice:
    """One causally observed close in the sweep-to-decision event path."""

    ts_ns: int
    close: float


@dataclass(frozen=True, slots=True)
class CausalScalar:
    """A scalar whose point-in-time visibility can be checked."""

    observed_time_ns: int
    value: float | None


@dataclass(frozen=True, slots=True)
class CrossMarketAuctionRoles:
    symbol: str
    side: str
    sweep_time_ns: int
    decision_time_ns: int
    synchronized_event_complete: bool
    liquidity_leader: str | None
    is_liquidity_leader: bool | None
    liquidity_observed_time_ns: int | None
    signed_event_returns: dict[str, float]
    event_direction_rank: int | None
    event_leadership_role: EventLeadershipRole
    peer_participation: PeerParticipation
    aligned_peer_count: int | None
    peer_count: int | None
    local_event_path_efficiency: float | None
    trailing_direction_scores: dict[str, float]
    trailing_direction_rank: int | None
    trailing_auction_role: TrailingAuctionRole
    trailing_observed_time_ns: int | None
    candidate_auction_progress_units: float | None
    market_auction_progress_units: float | None
    auction_progress_observed_time_ns: int | None
    accepted_repricing_phase: AcceptedRepricingPhase

    @property
    def independently_leads_event(self) -> bool | None:
        if self.event_leadership_role is EventLeadershipRole.UNKNOWN:
            return None
        return self.event_leadership_role is EventLeadershipRole.INDEPENDENT_LEAD

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["event_leadership_role"] = self.event_leadership_role.value
        payload["peer_participation"] = self.peer_participation.value
        payload["trailing_auction_role"] = self.trailing_auction_role.value
        payload["accepted_repricing_phase"] = self.accepted_repricing_phase.value
        payload["independently_leads_event"] = self.independently_leads_event
        return payload


def _side_sign(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(f"unsupported side: {side}")


def _validated_path(
    path: Sequence[EventPrice],
    *,
    decision_time_ns: int,
) -> tuple[EventPrice, ...]:
    points = tuple(path)
    previous = -1
    for point in points:
        if point.ts_ns <= previous:
            raise ValueError("event paths must have unique increasing timestamps")
        if point.ts_ns > decision_time_ns:
            raise ValueError("event paths cannot contain future observations")
        if not math.isfinite(float(point.close)) or float(point.close) <= 0.0:
            raise ValueError("event path closes must be finite and positive")
        previous = point.ts_ns
    return points


def _complete_event_return(
    path: Sequence[EventPrice],
    *,
    sweep_time_ns: int,
    decision_time_ns: int,
    sign: float,
) -> float | None:
    if (
        len(path) < 2
        or path[0].ts_ns != sweep_time_ns
        or path[-1].ts_ns != decision_time_ns
    ):
        return None
    return sign * (float(path[-1].close) / float(path[0].close) - 1.0)


def _path_efficiency(path: Sequence[EventPrice], sign: float) -> float | None:
    if len(path) < 2:
        return None
    increments = [
        math.log(float(right.close) / float(left.close))
        for left, right in zip(path, path[1:])
    ]
    travel = sum(abs(value) for value in increments)
    if travel <= EPS:
        return None
    return sign * sum(increments) / travel


def _peer_participation(
    candidate: str,
    signed_returns: Mapping[str, float],
) -> tuple[PeerParticipation, int | None, int | None]:
    peers = [float(value) for symbol, value in signed_returns.items() if symbol != candidate]
    if not peers:
        return PeerParticipation.UNKNOWN, None, None
    aligned = [value for value in peers if value > 0.0]
    if len(aligned) == len(peers):
        return PeerParticipation.UNANIMOUS, len(aligned), len(peers)
    if not aligned:
        return PeerParticipation.NONE_ALIGNED, 0, len(peers)

    # Candidate-13's quorum was not a vote count alone: aligned peers also had
    # to dominate every dissenting peer in absolute event displacement.
    dissent = [-value for value in peers if value <= 0.0]
    required = len(peers) // 2 + 1
    dominant = (
        len(aligned) >= required
        and bool(dissent)
        and max(dissent) < min(aligned)
    )
    role = PeerParticipation.DOMINANT_QUORUM if dominant else PeerParticipation.DIVIDED
    return role, len(aligned), len(peers)


def _event_role(
    *,
    symbol: str,
    signed_returns: Mapping[str, float],
    event_rank: int,
    peers: PeerParticipation,
) -> EventLeadershipRole:
    candidate = float(signed_returns[symbol])
    peer_values = [float(value) for key, value in signed_returns.items() if key != symbol]
    if any(math.isclose(candidate, value, rel_tol=1e-12, abs_tol=1e-15) for value in peer_values):
        if event_rank == 1:
            return EventLeadershipRole.CO_LEAD
    if event_rank == len(signed_returns):
        return EventLeadershipRole.LAGGARD
    if event_rank != 1:
        return EventLeadershipRole.FOLLOWER
    if peers in {PeerParticipation.DIVIDED, PeerParticipation.NONE_ALIGNED}:
        return EventLeadershipRole.INDEPENDENT_LEAD
    if peers is PeerParticipation.UNANIMOUS:
        return EventLeadershipRole.UNANIMOUS_LEAD
    if peers is PeerParticipation.DOMINANT_QUORUM:
        return EventLeadershipRole.QUORUM_LEAD
    return EventLeadershipRole.UNKNOWN


def _trailing_state(
    *,
    symbols: tuple[str, ...],
    symbol: str,
    side: str,
    sweep_time_ns: int,
    contexts: Mapping[str, DirectionalContext] | None,
) -> tuple[dict[str, float], int | None, TrailingAuctionRole, int | None]:
    if contexts is None or set(contexts) != set(symbols):
        return {}, None, TrailingAuctionRole.UNKNOWN, None
    observation_times: set[int] = set()
    scores: dict[str, float] = {}
    for name in symbols:
        context = contexts[name]
        if context.symbol != name or context.side != side:
            raise ValueError("directional context identity does not match the auction")
        if context.decision_time_ns > sweep_time_ns:
            raise ValueError("directional context cannot observe after the sweep")
        observation_times.add(int(context.decision_time_ns))
        if context.trend_alignment is None or not math.isfinite(context.trend_alignment):
            return {}, None, TrailingAuctionRole.UNKNOWN, None
        scores[name] = float(context.trend_alignment)
    if len(observation_times) != 1:
        return {}, None, TrailingAuctionRole.UNKNOWN, None
    rank = 1 + sum(value > scores[symbol] for name, value in scores.items() if name != symbol)
    if rank == 1:
        role = TrailingAuctionRole.LEADER
    elif rank == len(symbols):
        role = TrailingAuctionRole.LAGGARD
    elif rank <= (len(symbols) + 1) // 2:
        role = TrailingAuctionRole.FRONT_COHORT
    else:
        role = TrailingAuctionRole.INTERMEDIATE
    return scores, rank, role, observation_times.pop()


def _causal_scalar_snapshot(
    symbols: tuple[str, ...],
    observations: Mapping[str, CausalScalar | None] | None,
    *,
    latest_time_ns: int,
) -> tuple[dict[str, float], int | None]:
    if observations is None or set(observations) != set(symbols):
        return {}, None
    values: dict[str, float] = {}
    observed_times: set[int] = set()
    for symbol in symbols:
        observation = observations[symbol]
        if observation is None:
            return {}, None
        if observation.observed_time_ns > latest_time_ns:
            raise ValueError("cross-market scalar cannot observe after its causal boundary")
        observed_times.add(int(observation.observed_time_ns))
        raw = observation.value
        if raw is None or not math.isfinite(float(raw)):
            return {}, None
        values[symbol] = float(raw)
    if len(observed_times) != 1:
        return {}, None
    return values, observed_times.pop()


def _liquidity_leader(
    symbols: tuple[str, ...],
    trailing_quote_notionals: Mapping[str, CausalScalar | None] | None,
    *,
    sweep_time_ns: int,
) -> tuple[str | None, int | None]:
    values, observed_time = _causal_scalar_snapshot(
        symbols,
        trailing_quote_notionals,
        latest_time_ns=sweep_time_ns,
    )
    if not values or any(value < 0.0 for value in values.values()):
        return None, observed_time
    return sorted(symbols, key=lambda name: (-values[name], name))[0], observed_time


def _repricing_phase(
    *,
    symbols: tuple[str, ...],
    symbol: str,
    signed_returns: Mapping[str, float],
    peer_participation: PeerParticipation,
    event_rank: int | None,
    trailing_rank: int | None,
    trailing_scores: Mapping[str, float],
    liquidity_leader: str | None,
    auction_progress_units: Mapping[str, float],
) -> tuple[float | None, float | None, AcceptedRepricingPhase]:
    if not signed_returns or event_rank is None:
        return None, None, AcceptedRepricingPhase.UNKNOWN
    if peer_participation is not PeerParticipation.UNANIMOUS or signed_returns[symbol] <= 0.0:
        return None, None, AcceptedRepricingPhase.NOT_ACCEPTED
    if trailing_rank is None or set(trailing_scores) != set(symbols):
        return None, None, AcceptedRepricingPhase.UNKNOWN
    market_score = float(median(trailing_scores.values()))
    if trailing_scores[symbol] <= 0.0 or market_score <= 0.0:
        return None, None, AcceptedRepricingPhase.TRAILING_AUCTION_NOT_ALIGNED

    # V4 treated completion-last as a distinct transfer only when the symbol
    # was neither the trailing laggard nor the liquidity-concentration leader.
    if event_rank == len(symbols) and trailing_rank < len(symbols):
        if liquidity_leader is None:
            return None, None, AcceptedRepricingPhase.UNKNOWN
        if symbol != liquidity_leader:
            return None, None, AcceptedRepricingPhase.LAGGARD_TRANSFER
    if trailing_rank == len(symbols):
        return None, None, AcceptedRepricingPhase.UNKNOWN

    if set(auction_progress_units) != set(symbols):
        return None, None, AcceptedRepricingPhase.UNKNOWN
    candidate_progress = auction_progress_units[symbol]
    market_progress = float(median(auction_progress_units.values()))
    if candidate_progress <= 0.0 or market_progress <= 0.0:
        return (
            candidate_progress,
            market_progress,
            AcceptedRepricingPhase.TRAILING_AUCTION_NOT_ALIGNED,
        )
    phase = (
        AcceptedRepricingPhase.ALREADY_EXTENDED
        if candidate_progress >= 1.0 or market_progress >= 1.0
        else AcceptedRepricingPhase.EARLY_ACCEPTED_REPRICING
    )
    return candidate_progress, market_progress, phase


def analyze_cross_market_roles(
    *,
    symbols: Sequence[str],
    symbol: str,
    side: str,
    sweep_time_ns: int,
    decision_time_ns: int,
    event_paths: Mapping[str, Sequence[EventPrice]],
    directional_contexts: Mapping[str, DirectionalContext] | None = None,
    trailing_quote_notionals: Mapping[str, CausalScalar | None] | None = None,
    auction_progress_units: Mapping[str, CausalScalar | None] | None = None,
) -> CrossMarketAuctionRoles:
    """Describe cross-market auction roles using observations known by decision.

    ``auction_progress_units`` has a semantic contract instead of a fitted
    floor.  Each value is direction-signed progress divided by the caller's
    causally established completed-auction unit.  The function has no approve,
    reject, quality-score or position-sizing output.
    """

    names = tuple(symbols)
    if len(names) < 3 or len(set(names)) != len(names):
        raise ValueError("cross-market roles require at least three unique symbols")
    if symbol not in names:
        raise ValueError(f"unsupported symbol: {symbol}")
    if sweep_time_ns < 0 or sweep_time_ns >= decision_time_ns:
        raise ValueError("sweep_time_ns must precede decision_time_ns")
    sign = _side_sign(side)

    paths = {
        name: _validated_path(event_paths.get(name, ()), decision_time_ns=decision_time_ns)
        for name in names
    }
    signed_returns: dict[str, float] = {}
    for name, path in paths.items():
        value = _complete_event_return(
            path,
            sweep_time_ns=sweep_time_ns,
            decision_time_ns=decision_time_ns,
            sign=sign,
        )
        if value is not None:
            signed_returns[name] = value
    event_complete = len(signed_returns) == len(names)
    local_path = paths[symbol]
    local_efficiency = (
        _path_efficiency(local_path, sign)
        if local_path
        and local_path[0].ts_ns == sweep_time_ns
        and local_path[-1].ts_ns == decision_time_ns
        else None
    )

    if event_complete:
        event_rank = 1 + sum(
            value > signed_returns[symbol]
            for name, value in signed_returns.items()
            if name != symbol
        )
        peer_role, aligned_count, peer_count = _peer_participation(symbol, signed_returns)
        event_role = _event_role(
            symbol=symbol,
            signed_returns=signed_returns,
            event_rank=event_rank,
            peers=peer_role,
        )
    else:
        event_rank = None
        peer_role = PeerParticipation.UNKNOWN
        aligned_count = None
        peer_count = None
        event_role = EventLeadershipRole.UNKNOWN

    trailing_scores, trailing_rank, trailing_role, trailing_time = _trailing_state(
        symbols=names,
        symbol=symbol,
        side=side,
        sweep_time_ns=sweep_time_ns,
        contexts=directional_contexts,
    )
    liquidity_leader, liquidity_time = _liquidity_leader(
        names,
        trailing_quote_notionals,
        sweep_time_ns=sweep_time_ns,
    )
    progress_values, progress_time = _causal_scalar_snapshot(
        names,
        auction_progress_units,
        latest_time_ns=sweep_time_ns,
    )
    candidate_progress, market_progress, repricing_phase = _repricing_phase(
        symbols=names,
        symbol=symbol,
        signed_returns=signed_returns if event_complete else {},
        peer_participation=peer_role,
        event_rank=event_rank,
        trailing_rank=trailing_rank,
        trailing_scores=trailing_scores,
        liquidity_leader=liquidity_leader,
        auction_progress_units=progress_values,
    )
    return CrossMarketAuctionRoles(
        symbol=symbol,
        side=side,
        sweep_time_ns=int(sweep_time_ns),
        decision_time_ns=int(decision_time_ns),
        synchronized_event_complete=event_complete,
        liquidity_leader=liquidity_leader,
        is_liquidity_leader=None if liquidity_leader is None else liquidity_leader == symbol,
        liquidity_observed_time_ns=liquidity_time,
        signed_event_returns=dict(sorted(signed_returns.items())),
        event_direction_rank=event_rank,
        event_leadership_role=event_role,
        peer_participation=peer_role,
        aligned_peer_count=aligned_count,
        peer_count=peer_count,
        local_event_path_efficiency=local_efficiency,
        trailing_direction_scores=dict(sorted(trailing_scores.items())),
        trailing_direction_rank=trailing_rank,
        trailing_auction_role=trailing_role,
        trailing_observed_time_ns=trailing_time,
        candidate_auction_progress_units=candidate_progress,
        market_auction_progress_units=market_progress,
        auction_progress_observed_time_ns=progress_time,
        accepted_repricing_phase=repricing_phase,
    )


__all__ = [
    "AcceptedRepricingPhase",
    "CausalScalar",
    "CrossMarketAuctionRoles",
    "EventLeadershipRole",
    "EventPrice",
    "PeerParticipation",
    "SourceOwnershipDecision",
    "SourceOwnershipRole",
    "TrailingAuctionRole",
    "analyze_cross_market_roles",
    "classify_source_ownership",
]
