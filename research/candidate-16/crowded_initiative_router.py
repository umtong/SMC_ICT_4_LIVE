"""Pure causal router for Candidate 16 v5 crowded-initiative rejection.

A completed one-minute impulse is only a *state observation*.  It becomes a
tradeable rejection only after a strictly later completed bar shows price,
aggressor flow, and closing L1 pressure moving against that impulse.

The shock qualifier uses no fitted return threshold.  Its minimum displacement
is the complete configured round-trip friction, so an event which cannot even
span its modeled costs is not an economic initiative.  Positive OI change marks
new participation rather than liquidation-only flow, while opposite closing L1
pressure marks resistance at the best quotes.  No order is created here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import math


class CrowdedDecision(StrEnum):
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class CrowdedShockObservation:
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
class CrowdedShockQualification:
    qualified: bool
    reason: str
    shock_direction: int
    fade_side: int
    economic_floor_bps: float


@dataclass(frozen=True, slots=True)
class LaterFailureObservation:
    bar_index: int
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    flow_60s: float
    l1_imbalance_close: float


@dataclass(frozen=True, slots=True)
class CrowdedShockState:
    scenario_id: str
    shock_direction: int
    fade_side: int
    shock_index: int
    last_index: int
    expires_index: int
    shock_open: float
    shock_high: float
    shock_low: float
    shock_close: float
    atr: float
    observations: int = 0
    decision: CrowdedDecision = CrowdedDecision.WAITING
    reason: str = "CROWDED_INITIATIVE_FROZEN_NO_ORDER"


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def qualify_crowded_shock(
    observation: CrowdedShockObservation,
    *,
    economic_floor_bps: float,
    minimum_notional_burst: float,
    maximum_metrics_age_seconds: float,
) -> CrowdedShockQualification:
    """Classify one completed impulse without consulting later prices or PnL."""
    if economic_floor_bps <= 0.0 or not math.isfinite(economic_floor_bps):
        raise ValueError("economic_floor_bps must be positive and finite")
    if minimum_notional_burst < 1.0 or not math.isfinite(minimum_notional_burst):
        raise ValueError("minimum_notional_burst must be finite and at least one")
    if maximum_metrics_age_seconds < 0.0 or not math.isfinite(
        maximum_metrics_age_seconds,
    ):
        raise ValueError("maximum_metrics_age_seconds must be finite and non-negative")
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
        return CrowdedShockQualification(
            False,
            "CROWDED_SHOCK_REQUIRED_OBSERVATION_MISSING",
            0,
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
        return CrowdedShockQualification(
            False,
            "CROWDED_SHOCK_INVALID_PRICE_GEOMETRY",
            0,
            0,
            economic_floor_bps,
        )

    direction = 1 if observation.ret_60s_bps > 0.0 else (
        -1 if observation.ret_60s_bps < 0.0 else 0
    )
    if direction == 0 or abs(observation.ret_60s_bps) < economic_floor_bps:
        return CrowdedShockQualification(
            False,
            "INITIATIVE_DISPLACEMENT_DID_NOT_CLEAR_MODELED_ROUND_TRIP_FRICTION",
            direction,
            -direction,
            economic_floor_bps,
        )
    if observation.notional_burst < minimum_notional_burst:
        return CrowdedShockQualification(
            False,
            "INITIATIVE_NOTIONAL_NOT_ABOVE_CAUSAL_BASELINE",
            direction,
            -direction,
            economic_floor_bps,
        )
    if direction * observation.flow_60s <= 0.0:
        return CrowdedShockQualification(
            False,
            "INITIATIVE_PRICE_AND_AGGRESSOR_FLOW_DISAGREE",
            direction,
            -direction,
            economic_floor_bps,
        )
    if observation.metrics_age_seconds > maximum_metrics_age_seconds:
        return CrowdedShockQualification(
            False,
            "OPEN_INTEREST_OBSERVATION_STALE",
            direction,
            -direction,
            economic_floor_bps,
        )
    if observation.oi_change_5m <= 0.0:
        return CrowdedShockQualification(
            False,
            "INITIATIVE_DID_NOT_ADD_OPEN_INTEREST",
            direction,
            -direction,
            economic_floor_bps,
        )
    if direction * observation.l1_imbalance_close >= 0.0:
        return CrowdedShockQualification(
            False,
            "CLOSING_L1_PRESSURE_DID_NOT_RESIST_INITIATIVE",
            direction,
            -direction,
            economic_floor_bps,
        )
    return CrowdedShockQualification(
        True,
        "NEW_POSITION_INITIATIVE_MET_OPPOSING_CLOSING_L1_PRESSURE",
        direction,
        -direction,
        economic_floor_bps,
    )


def advance_crowded_shock(
    state: CrowdedShockState,
    observation: LaterFailureObservation,
    *,
    maximum_close_extension_atr: float,
) -> CrowdedShockState:
    """Advance a frozen shock with a strictly later independent observation."""
    if state.decision is not CrowdedDecision.WAITING:
        raise ValueError("terminal crowded-shock state cannot be advanced")
    if state.shock_direction not in (-1, 1) or state.fade_side != -state.shock_direction:
        raise ValueError("invalid crowded-shock direction")
    if maximum_close_extension_atr < 0.0 or not math.isfinite(
        maximum_close_extension_atr,
    ):
        raise ValueError("maximum_close_extension_atr must be finite and non-negative")
    if observation.bar_index <= state.last_index:
        raise ValueError("later failure observation must be strictly newer")
    if observation.bar_index <= state.shock_index:
        raise ValueError("shock bar cannot confirm itself")
    if not _finite(
        observation.open,
        observation.high,
        observation.low,
        observation.close,
        observation.flow_60s,
        observation.l1_imbalance_close,
    ):
        raise ValueError("later failure observation must be finite")

    observations = state.observations + 1
    if observation.bar_index > state.expires_index:
        return replace(
            state,
            last_index=observation.bar_index,
            observations=observations,
            decision=CrowdedDecision.EXPIRED,
            reason="CROWDED_INITIATIVE_FAILURE_WINDOW_EXPIRED",
        )

    extension = (
        observation.close > state.shock_high
        + maximum_close_extension_atr * state.atr
        if state.shock_direction > 0
        else observation.close < state.shock_low
        - maximum_close_extension_atr * state.atr
    )
    if extension:
        return replace(
            state,
            last_index=observation.bar_index,
            observations=observations,
            decision=CrowdedDecision.INVALIDATED,
            reason="INITIATIVE_EXTREME_ACCEPTED_BEFORE_FAILURE_CONFIRMATION",
        )

    side = state.fade_side
    directional_bar = side * (observation.close - observation.open) > 0.0
    shock_close_reclaimed = side * (
        observation.close - state.shock_close
    ) > 0.0
    counter_aggressor_flow = side * observation.flow_60s > 0.0
    counter_l1_pressure = side * observation.l1_imbalance_close > 0.0
    if (
        directional_bar
        and shock_close_reclaimed
        and counter_aggressor_flow
        and counter_l1_pressure
    ):
        return replace(
            state,
            last_index=observation.bar_index,
            observations=observations,
            decision=CrowdedDecision.CONFIRMED,
            reason=(
                "STRICTLY_LATER_PRICE_FLOW_AND_L1_PRESSURE_CONFIRMED_"
                "CROWDED_INITIATIVE_FAILURE"
            ),
        )

    if observation.bar_index >= state.expires_index:
        return replace(
            state,
            last_index=observation.bar_index,
            observations=observations,
            decision=CrowdedDecision.EXPIRED,
            reason="CROWDED_INITIATIVE_DID_NOT_FAIL_WITHIN_CAUSAL_WINDOW",
        )
    return replace(
        state,
        last_index=observation.bar_index,
        observations=observations,
        reason="CROWDED_INITIATIVE_FROZEN_WAITING_FOR_INDEPENDENT_FAILURE",
    )


__all__ = [
    "CrowdedDecision",
    "CrowdedShockObservation",
    "CrowdedShockQualification",
    "CrowdedShockState",
    "LaterFailureObservation",
    "advance_crowded_shock",
    "qualify_crowded_shock",
]
