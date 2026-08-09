"""Sequential evidence for a persistent aggressor-flow repricing regime."""
from __future__ import annotations

from dataclasses import dataclass
import math


NULL_DIRECTION_PROBABILITY = 0.5
ALTERNATIVE_DIRECTION_PROBABILITY = 2.0 / 3.0
TYPE_I_ERROR = 0.05
TYPE_II_ERROR = 0.05
UPPER_LOG_LIKELIHOOD_BOUNDARY = math.log((1.0 - TYPE_II_ERROR) / TYPE_I_ERROR)
SUCCESS_LOG_LIKELIHOOD = math.log(
    ALTERNATIVE_DIRECTION_PROBABILITY / NULL_DIRECTION_PROBABILITY,
)
FAILURE_LOG_LIKELIHOOD = math.log(
    (1.0 - ALTERNATIVE_DIRECTION_PROBABILITY)
    / (1.0 - NULL_DIRECTION_PROBABILITY),
)


@dataclass(frozen=True, slots=True)
class SequentialFlowState:
    upward_log_likelihood: float = 0.0
    downward_log_likelihood: float = 0.0
    informative_observations: int = 0
    first_index: int = -1
    last_index: int = -1
    range_high: float = -math.inf
    range_low: float = math.inf


@dataclass(frozen=True, slots=True)
class SequentialFlowUpdate:
    state: SequentialFlowState
    decision: int
    informative: bool


def update_sequential_flow(
    *,
    state: SequentialFlowState,
    flow_60s: float,
    high: float,
    low: float,
    bar_index: int,
    minimum_absolute_flow: float,
) -> SequentialFlowUpdate:
    """Update mirrored restarted likelihoods from one completed minute.

    Each informative minute is a directional observation. The likelihood ratio
    compares a 2:1 directional regime with a 1:1 null. Evidence against a side
    restarts that side at zero; the opposite side continues symmetrically. This
    is a repeated online change detector, not a fitted score.
    """
    values = (flow_60s, high, low, minimum_absolute_flow)
    if not all(math.isfinite(float(value)) for value in values):
        return SequentialFlowUpdate(state=state, decision=0, informative=False)
    if minimum_absolute_flow < 0.0 or high < low:
        raise ValueError("invalid sequential flow observation")
    if abs(flow_60s) < minimum_absolute_flow:
        return SequentialFlowUpdate(state=state, decision=0, informative=False)

    up_increment = SUCCESS_LOG_LIKELIHOOD if flow_60s > 0.0 else FAILURE_LOG_LIKELIHOOD
    down_increment = SUCCESS_LOG_LIKELIHOOD if flow_60s < 0.0 else FAILURE_LOG_LIKELIHOOD
    upward = max(0.0, state.upward_log_likelihood + up_increment)
    downward = max(0.0, state.downward_log_likelihood + down_increment)
    first_index = bar_index if state.informative_observations == 0 else state.first_index
    updated = SequentialFlowState(
        upward_log_likelihood=upward,
        downward_log_likelihood=downward,
        informative_observations=state.informative_observations + 1,
        first_index=first_index,
        last_index=bar_index,
        range_high=max(state.range_high, high),
        range_low=min(state.range_low, low),
    )
    decision = 1 if upward >= UPPER_LOG_LIKELIHOOD_BOUNDARY else -1 if downward >= UPPER_LOG_LIKELIHOOD_BOUNDARY else 0
    return SequentialFlowUpdate(state=updated, decision=decision, informative=True)


def sequential_release_breakout(
    *,
    side: int,
    prior_high: float,
    prior_low: float,
    open_price: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    flow_60s: float,
    efficiency_60s: float,
    notional_burst: float,
    bid_depth_change_1m: float,
    ask_depth_change_1m: float,
    minimum_break_distance_atr: float,
    minimum_flow: float,
    minimum_efficiency: float,
    minimum_notional_burst: float,
    minimum_depth_withdrawal: float,
    minimum_close_location: float,
) -> bool:
    """Whether accumulated flow became efficient price discovery."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        prior_high,
        prior_low,
        open_price,
        high,
        low,
        close,
        atr,
        flow_60s,
        efficiency_60s,
        notional_burst,
        bid_depth_change_1m,
        ask_depth_change_1m,
        minimum_break_distance_atr,
        minimum_flow,
        minimum_efficiency,
        minimum_notional_burst,
        minimum_depth_withdrawal,
        minimum_close_location,
    )
    if not all(math.isfinite(float(value)) for value in values) or atr <= 0.0:
        return False
    boundary = prior_high if side > 0 else prior_low
    break_distance = side * (close - boundary) / atr
    span = max(high - low, 1e-12)
    close_location = (close - low) / span if side > 0 else (high - close) / span
    threatened_depth_change = ask_depth_change_1m if side > 0 else bid_depth_change_1m
    return (
        prior_high > prior_low
        and break_distance >= minimum_break_distance_atr
        and side * flow_60s >= minimum_flow
        and efficiency_60s >= minimum_efficiency
        and notional_burst >= minimum_notional_burst
        and -threatened_depth_change >= minimum_depth_withdrawal
        and close_location >= minimum_close_location
        and side * (close - open_price) > 0.0
    )


def first_sequential_boundary_retest(
    *,
    side: int,
    boundary: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    maximum_counterflow: float,
    minimum_directional_depth: float,
) -> bool:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        boundary,
        high,
        low,
        close,
        flow_15s,
        depth_imbalance,
        maximum_counterflow,
        minimum_directional_depth,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    touched = low <= boundary if side > 0 else high >= boundary
    defended = close > boundary if side > 0 else close < boundary
    return (
        touched
        and defended
        and side * flow_15s >= -maximum_counterflow
        and side * depth_imbalance >= minimum_directional_depth
    )


def sequential_structural_stop(
    *,
    side: int,
    evidence_high: float,
    evidence_low: float,
    atr: float,
    stop_buffer_atr: float,
) -> float:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not all(math.isfinite(value) for value in (evidence_high, evidence_low, atr, stop_buffer_atr)):
        return float("nan")
    return evidence_low - stop_buffer_atr * atr if side > 0 else evidence_high + stop_buffer_atr * atr


__all__ = [
    "ALTERNATIVE_DIRECTION_PROBABILITY",
    "FAILURE_LOG_LIKELIHOOD",
    "NULL_DIRECTION_PROBABILITY",
    "SUCCESS_LOG_LIKELIHOOD",
    "SequentialFlowState",
    "SequentialFlowUpdate",
    "TYPE_I_ERROR",
    "TYPE_II_ERROR",
    "UPPER_LOG_LIKELIHOOD_BOUNDARY",
    "first_sequential_boundary_retest",
    "sequential_release_breakout",
    "sequential_structural_stop",
    "update_sequential_flow",
]
