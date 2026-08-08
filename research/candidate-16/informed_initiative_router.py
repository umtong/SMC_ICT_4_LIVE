"""Pure causal router for Candidate 16 v6 informed-initiative continuation.

A completed impulse qualifies only when it clears modeled round-trip friction,
adds open interest, and closes with aggressor flow and best-quote pressure in
the same direction.  It is frozen without an order.  A later counter-direction
bar must hold the impulse midpoint, and a still later bar must close through the
pullback boundary with renewed directional flow and L1 pressure.  That breakout
is a new auction leg; it is not a delayed entry into the original shock.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class ContinuationDecision(StrEnum):
    WAITING_PULLBACK = "WAITING_PULLBACK"
    PULLBACK_ARMED = "PULLBACK_ARMED"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class InformedInitiativeObservation:
    bar_index: int
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    atr: float
    ret_60s_bps: float
    flow_60s: float
    notional_burst: float
    oi_change_5m: float
    metrics_age_seconds: float
    l1_imbalance_close: float


@dataclass(frozen=True, slots=True)
class InformedInitiativeQualification:
    qualified: bool
    reason: str
    direction: int
    economic_floor_bps: float


@dataclass(frozen=True, slots=True)
class LaterContinuationObservation:
    bar_index: int
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    flow_60s: float
    l1_imbalance_close: float


@dataclass(frozen=True, slots=True)
class InformedContinuationState:
    scenario_id: str
    direction: int
    shock_index: int
    last_index: int
    expires_index: int
    shock_open: float
    shock_high: float
    shock_low: float
    shock_close: float
    midpoint: float
    atr: float
    pullback_index: int = -1
    pullback_extreme: float = float("nan")
    pullback_boundary: float = float("nan")
    observations: int = 0
    decision: ContinuationDecision = ContinuationDecision.WAITING_PULLBACK
    reason: str = "INFORMED_INITIATIVE_FROZEN_WAITING_FOR_PULLBACK"


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def qualify_informed_initiative(
    observation: InformedInitiativeObservation,
    *,
    economic_floor_bps: float,
    minimum_notional_burst: float,
    maximum_metrics_age_seconds: float,
) -> InformedInitiativeQualification:
    if economic_floor_bps <= 0.0 or not math.isfinite(economic_floor_bps):
        raise ValueError("economic_floor_bps must be positive and finite")
    if minimum_notional_burst < 1.0 or not math.isfinite(minimum_notional_burst):
        raise ValueError("minimum_notional_burst must be at least one")
    if maximum_metrics_age_seconds < 0.0 or not math.isfinite(
        maximum_metrics_age_seconds,
    ):
        raise ValueError("maximum_metrics_age_seconds must be non-negative")
    if observation.bar_index < 0 or observation.ts_event <= 0:
        raise ValueError("observation identity must be positive")
    if not _finite(
        observation.open,
        observation.high,
        observation.low,
        observation.close,
        observation.atr,
        observation.ret_60s_bps,
        observation.flow_60s,
        observation.notional_burst,
        observation.oi_change_5m,
        observation.metrics_age_seconds,
        observation.l1_imbalance_close,
    ):
        return InformedInitiativeQualification(
            False,
            "INFORMED_INITIATIVE_REQUIRED_OBSERVATION_MISSING",
            0,
            economic_floor_bps,
        )
    if (
        observation.open <= 0.0
        or observation.low <= 0.0
        or observation.high < observation.low
        or not observation.low <= observation.close <= observation.high
        or observation.atr <= 0.0
    ):
        return InformedInitiativeQualification(
            False,
            "INFORMED_INITIATIVE_INVALID_PRICE_GEOMETRY",
            0,
            economic_floor_bps,
        )

    direction = 1 if observation.ret_60s_bps > 0.0 else (
        -1 if observation.ret_60s_bps < 0.0 else 0
    )
    if direction == 0 or abs(observation.ret_60s_bps) < economic_floor_bps:
        return InformedInitiativeQualification(
            False,
            "INITIATIVE_DISPLACEMENT_DID_NOT_CLEAR_MODELED_ROUND_TRIP_FRICTION",
            direction,
            economic_floor_bps,
        )
    if observation.notional_burst < minimum_notional_burst:
        return InformedInitiativeQualification(
            False,
            "INITIATIVE_NOTIONAL_NOT_ABOVE_CAUSAL_BASELINE",
            direction,
            economic_floor_bps,
        )
    if direction * observation.flow_60s <= 0.0:
        return InformedInitiativeQualification(
            False,
            "INITIATIVE_PRICE_AND_AGGRESSOR_FLOW_DISAGREE",
            direction,
            economic_floor_bps,
        )
    if observation.metrics_age_seconds > maximum_metrics_age_seconds:
        return InformedInitiativeQualification(
            False,
            "OPEN_INTEREST_OBSERVATION_STALE",
            direction,
            economic_floor_bps,
        )
    if observation.oi_change_5m <= 0.0:
        return InformedInitiativeQualification(
            False,
            "INITIATIVE_DID_NOT_ADD_OPEN_INTEREST",
            direction,
            economic_floor_bps,
        )
    if direction * observation.l1_imbalance_close <= 0.0:
        return InformedInitiativeQualification(
            False,
            "CLOSING_L1_PRESSURE_DID_NOT_SUPPORT_INITIATIVE",
            direction,
            economic_floor_bps,
        )
    return InformedInitiativeQualification(
        True,
        "NEW_POSITION_INITIATIVE_WITH_ALIGNED_AGGRESSOR_AND_L1_PRESSURE",
        direction,
        economic_floor_bps,
    )


def _more_adverse_extreme(
    direction: int,
    existing: float,
    observation: LaterContinuationObservation,
) -> float:
    candidate = observation.low if direction > 0 else observation.high
    if not math.isfinite(existing):
        return candidate
    return min(existing, candidate) if direction > 0 else max(existing, candidate)


def _wider_pullback_boundary(
    direction: int,
    existing: float,
    observation: LaterContinuationObservation,
) -> float:
    candidate = observation.high if direction > 0 else observation.low
    if not math.isfinite(existing):
        return candidate
    return max(existing, candidate) if direction > 0 else min(existing, candidate)


def advance_informed_continuation(
    state: InformedContinuationState,
    observation: LaterContinuationObservation,
) -> InformedContinuationState:
    if state.decision in {
        ContinuationDecision.CONFIRMED,
        ContinuationDecision.INVALIDATED,
        ContinuationDecision.EXPIRED,
    }:
        raise ValueError("terminal informed-continuation state cannot be advanced")
    if state.direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if observation.bar_index <= state.last_index:
        raise ValueError("later continuation observation must be strictly newer")
    if observation.bar_index <= state.shock_index:
        raise ValueError("initiative bar cannot confirm itself")
    if not _finite(
        observation.open,
        observation.high,
        observation.low,
        observation.close,
        observation.flow_60s,
        observation.l1_imbalance_close,
    ):
        raise ValueError("later continuation observation must be finite")

    count = state.observations + 1
    if observation.bar_index > state.expires_index:
        return replace(
            state,
            last_index=observation.bar_index,
            observations=count,
            decision=ContinuationDecision.EXPIRED,
            reason="INFORMED_INITIATIVE_CONTINUATION_WINDOW_EXPIRED",
        )

    direction = state.direction
    if direction * (observation.close - state.midpoint) <= 0.0:
        return replace(
            state,
            last_index=observation.bar_index,
            observations=count,
            decision=ContinuationDecision.INVALIDATED,
            reason="INITIATIVE_MIDPOINT_ACCEPTED_AGAINST_DIRECTION",
        )

    if state.decision is ContinuationDecision.WAITING_PULLBACK:
        counter_bar = direction * (observation.close - observation.open) < 0.0
        if counter_bar:
            return replace(
                state,
                last_index=observation.bar_index,
                pullback_index=observation.bar_index,
                pullback_extreme=(
                    observation.low if direction > 0 else observation.high
                ),
                pullback_boundary=(
                    observation.high if direction > 0 else observation.low
                ),
                observations=count,
                decision=ContinuationDecision.PULLBACK_ARMED,
                reason="COUNTER_BAR_HELD_INITIATIVE_MIDPOINT",
            )
        if observation.bar_index >= state.expires_index:
            return replace(
                state,
                last_index=observation.bar_index,
                observations=count,
                decision=ContinuationDecision.EXPIRED,
                reason="NO_TRADEABLE_PULLBACK_BEFORE_EXPIRY",
            )
        return replace(
            state,
            last_index=observation.bar_index,
            observations=count,
            reason="INFORMED_INITIATIVE_WAITING_FOR_COUNTER_BAR",
        )

    if observation.bar_index <= state.pullback_index:
        raise ValueError("pullback bar cannot confirm itself")
    prior_boundary = state.pullback_boundary
    directional_bar = direction * (observation.close - observation.open) > 0.0
    boundary_broken = direction * (observation.close - prior_boundary) > 0.0
    directional_flow = direction * observation.flow_60s > 0.0
    directional_l1 = direction * observation.l1_imbalance_close > 0.0
    updated_extreme = _more_adverse_extreme(
        direction,
        state.pullback_extreme,
        observation,
    )
    if directional_bar and boundary_broken and directional_flow and directional_l1:
        return replace(
            state,
            last_index=observation.bar_index,
            pullback_extreme=updated_extreme,
            observations=count,
            decision=ContinuationDecision.CONFIRMED,
            reason=(
                "STRICTLY_LATER_PRICE_FLOW_AND_L1_BROKE_PULLBACK_BOUNDARY"
            ),
        )

    updated_boundary = _wider_pullback_boundary(
        direction,
        state.pullback_boundary,
        observation,
    )
    if observation.bar_index >= state.expires_index:
        return replace(
            state,
            last_index=observation.bar_index,
            pullback_extreme=updated_extreme,
            pullback_boundary=updated_boundary,
            observations=count,
            decision=ContinuationDecision.EXPIRED,
            reason="PULLBACK_DID_NOT_RESUME_WITHIN_CAUSAL_WINDOW",
        )
    return replace(
        state,
        last_index=observation.bar_index,
        pullback_extreme=updated_extreme,
        pullback_boundary=updated_boundary,
        observations=count,
        reason="PULLBACK_ARMED_WAITING_FOR_NEW_DIRECTIONAL_LEG",
    )


__all__ = [
    "ContinuationDecision",
    "InformedContinuationState",
    "InformedInitiativeObservation",
    "InformedInitiativeQualification",
    "LaterContinuationObservation",
    "advance_informed_continuation",
    "qualify_informed_initiative",
]
