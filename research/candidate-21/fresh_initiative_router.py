"""Causal fresh-initiative acceptance router for Candidate 21.

The event is not a candle-pattern entry. It identifies an auction in which
aggressive flow moves price efficiently *through opposing displayed depth*
while perpetual premium confirms and open interest remains in a moderate,
non-liquidation regime. A strictly later completed bar must then accept beyond
the event extreme. The target and invalidation are frozen before that later
confirmation, so confirmation cannot manufacture its own reward geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class FreshDecision(StrEnum):
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    TARGET_CONSUMED = "TARGET_CONSUMED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class FreshThresholds:
    minimum_alignment_bps: float = 15.0
    minimum_abs_flow_60s: float = 0.10
    minimum_notional_burst: float = 1.25
    maximum_notional_burst: float = 5.0
    minimum_efficiency: float = 0.17
    maximum_efficiency: float = 0.65
    maximum_directional_depth: float = -0.05
    minimum_oi_change_5m: float = -0.0007
    maximum_oi_change_5m: float = 0.0010
    minimum_directional_prior_30m_bps: float = -50.0
    maximum_directional_prior_30m_bps: float = 60.0
    minimum_directional_premium_change_5m: float = 0.0
    minimum_directional_premium_index: float = 0.0
    confirmation_close_location: float = 0.60
    maximum_wait_bars: int = 3


@dataclass(frozen=True, slots=True)
class FreshEvidence:
    flow_60s: float
    flow_price_alignment_60s: float
    notional_burst: float
    efficiency_60s: float
    depth_imbalance_1: float
    oi_change_5m: float
    premium_change_5m: float
    premium_index: float
    prior_30m_return_bps: float


@dataclass(frozen=True, slots=True)
class FreshSignal:
    side: int
    reason: str


@dataclass(frozen=True, slots=True)
class FreshEpisode:
    scenario_id: str
    side: int
    event_index: int
    expires_index: int
    event_high: float
    event_low: float
    event_close: float
    origin_price: float
    stop_price: float
    target_price: float
    target_pool_id: str
    observations: int = 0
    decision: FreshDecision = FreshDecision.WAITING
    reason: str = "FRESH_INITIATIVE_EVENT_AWAITS_STRICTLY_LATER_ACCEPTANCE"


@dataclass(frozen=True, slots=True)
class FreshObservation:
    bar_index: int
    open: float
    high: float
    low: float
    close: float


def _finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def classify_fresh_initiative(
    evidence: FreshEvidence,
    thresholds: FreshThresholds = FreshThresholds(),
) -> FreshSignal:
    """Return a symmetric direction only for fresh, non-forced acceptance."""
    values = (
        evidence.flow_60s,
        evidence.flow_price_alignment_60s,
        evidence.notional_burst,
        evidence.efficiency_60s,
        evidence.depth_imbalance_1,
        evidence.oi_change_5m,
        evidence.premium_change_5m,
        evidence.premium_index,
        evidence.prior_30m_return_bps,
    )
    if not _finite(*values) or evidence.flow_60s == 0.0:
        return FreshSignal(0, "INCOMPLETE_OR_DIRECTIONLESS_FRESH_INITIATIVE_EVIDENCE")
    side = 1 if evidence.flow_60s > 0.0 else -1
    directional_depth = side * evidence.depth_imbalance_1
    directional_prior = side * evidence.prior_30m_return_bps
    directional_premium_change = side * evidence.premium_change_5m
    directional_premium = side * evidence.premium_index

    if evidence.flow_price_alignment_60s < thresholds.minimum_alignment_bps:
        return FreshSignal(0, "AGGRESSIVE_FLOW_DID_NOT_MOVE_PRICE_ENOUGH")
    if abs(evidence.flow_60s) < thresholds.minimum_abs_flow_60s:
        return FreshSignal(0, "AGGRESSIVE_FLOW_SHARE_TOO_SMALL")
    if not (
        thresholds.minimum_notional_burst
        <= evidence.notional_burst
        <= thresholds.maximum_notional_burst
    ):
        return FreshSignal(0, "NOTIONAL_STATE_NOT_MODERATE_INITIATIVE")
    if not (
        thresholds.minimum_efficiency
        <= evidence.efficiency_60s
        <= thresholds.maximum_efficiency
    ):
        return FreshSignal(0, "AUCTION_EITHER_ABSORBED_OR_ALREADY_EXHAUSTED")
    if directional_depth > thresholds.maximum_directional_depth:
        return FreshSignal(0, "PRICE_DID_NOT_ADVANCE_THROUGH_OPPOSING_DISPLAYED_DEPTH")
    if not (
        thresholds.minimum_oi_change_5m
        <= evidence.oi_change_5m
        <= thresholds.maximum_oi_change_5m
    ):
        return FreshSignal(0, "OPEN_INTEREST_STATE_IS_FORCED_OR_CROWDED")
    if not (
        thresholds.minimum_directional_prior_30m_bps
        <= directional_prior
        <= thresholds.maximum_directional_prior_30m_bps
    ):
        return FreshSignal(0, "INITIATIVE_IS_ALREADY_DIRECTIONALLY_OVEREXTENDED")
    if directional_premium_change < thresholds.minimum_directional_premium_change_5m:
        return FreshSignal(0, "PERPETUAL_PREMIUM_CHANGE_DID_NOT_CONFIRM_DIRECTION")
    if directional_premium < thresholds.minimum_directional_premium_index:
        return FreshSignal(0, "PERPETUAL_PREMIUM_LEVEL_OPPOSED_DIRECTION")
    return FreshSignal(
        side,
        "FRESH_AGGRESSION_CONSUMED_OPPOSING_DEPTH_WITH_MODERATE_OI_AND_BASIS_CONFIRMATION",
    )


def advance_fresh_episode(
    episode: FreshEpisode,
    observation: FreshObservation,
    thresholds: FreshThresholds = FreshThresholds(),
) -> FreshEpisode:
    """Advance only with a strictly later completed bar.

    Target/stop completion is checked before confirmation. Thus a bar which has
    already consumed the objective can never be used to justify an entry.
    """
    if episode.decision is not FreshDecision.WAITING:
        return episode
    if observation.bar_index <= episode.event_index:
        return episode
    if not _finite(
        observation.open,
        observation.high,
        observation.low,
        observation.close,
    ) or observation.high < observation.low:
        return replace(
            episode,
            decision=FreshDecision.INVALIDATED,
            reason="INVALID_COMPLETED_BAR_GEOMETRY",
        )

    observations = episode.observations + 1
    target_consumed = (
        observation.high >= episode.target_price
        if episode.side > 0
        else observation.low <= episode.target_price
    )
    if target_consumed:
        return replace(
            episode,
            observations=observations,
            decision=FreshDecision.TARGET_CONSUMED,
            reason="FROZEN_NATURAL_TARGET_CONSUMED_BEFORE_ENTRY",
        )

    stop_reached = (
        observation.low <= episode.stop_price
        if episode.side > 0
        else observation.high >= episode.stop_price
    )
    origin_lost = (
        observation.close <= episode.origin_price
        if episode.side > 0
        else observation.close >= episode.origin_price
    )
    if stop_reached or origin_lost:
        return replace(
            episode,
            observations=observations,
            decision=FreshDecision.INVALIDATED,
            reason=(
                "FROZEN_STRUCTURAL_STOP_REACHED_BEFORE_ENTRY"
                if stop_reached
                else "EVENT_ORIGIN_NOT_ACCEPTED"
            ),
        )

    span = max(observation.high - observation.low, 1e-12)
    close_location = (
        (observation.close - observation.low) / span
        if episode.side > 0
        else (observation.high - observation.close) / span
    )
    directional_body = episode.side * (observation.close - observation.open)
    accepted_beyond_event = (
        observation.close > episode.event_high
        if episode.side > 0
        else observation.close < episode.event_low
    )
    if (
        accepted_beyond_event
        and directional_body > 0.0
        and close_location >= thresholds.confirmation_close_location
    ):
        return replace(
            episode,
            observations=observations,
            decision=FreshDecision.CONFIRMED,
            reason="STRICTLY_LATER_BAR_ACCEPTED_BEYOND_FROZEN_EVENT_EXTREME",
        )

    if observation.bar_index >= episode.expires_index:
        return replace(
            episode,
            observations=observations,
            decision=FreshDecision.EXPIRED,
            reason="FRESH_INITIATIVE_DID_NOT_ACCEPT_WITHIN_CAUSAL_WINDOW",
        )
    return replace(
        episode,
        observations=observations,
        reason="FRESH_INITIATIVE_REMAINS_UNRESOLVED",
    )


def mirror_evidence(evidence: FreshEvidence) -> FreshEvidence:
    """Mirror directional inputs for long/short symmetry tests."""
    return FreshEvidence(
        flow_60s=-evidence.flow_60s,
        flow_price_alignment_60s=evidence.flow_price_alignment_60s,
        notional_burst=evidence.notional_burst,
        efficiency_60s=evidence.efficiency_60s,
        depth_imbalance_1=-evidence.depth_imbalance_1,
        oi_change_5m=evidence.oi_change_5m,
        premium_change_5m=-evidence.premium_change_5m,
        premium_index=-evidence.premium_index,
        prior_30m_return_bps=-evidence.prior_30m_return_bps,
    )


__all__ = [
    "FreshDecision",
    "FreshEpisode",
    "FreshEvidence",
    "FreshObservation",
    "FreshSignal",
    "FreshThresholds",
    "advance_fresh_episode",
    "classify_fresh_initiative",
    "mirror_evidence",
]
