"""Pure market-state predicates for a reversal attempt followed by continuation."""
from __future__ import annotations

import math

from depth_logic import DIRECTIONAL_DEPTH_MIN
from external_acceptance_retest_logic import first_accepted_level_retest_response


def continuation_reacceptance_ready(
    *,
    continuation_side: int,
    sweep_extreme: float,
    open_price: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    flow_15s: float,
    flow_60s: float,
    efficiency_60s: float,
    notional_burst: float,
    bid_depth_change_1m: float,
    ask_depth_change_1m: float,
    minimum_close_distance_atr: float,
    minimum_flow: float,
    minimum_efficiency: float,
    minimum_notional_burst: float,
    minimum_depth_withdrawal: float,
    minimum_close_location: float,
) -> bool:
    """Return whether a completed bar efficiently reaccepted the raid direction."""
    if continuation_side not in (-1, 1):
        raise ValueError("continuation_side must be -1 or 1")
    values = (
        sweep_extreme,
        open_price,
        high,
        low,
        close,
        atr,
        flow_15s,
        flow_60s,
        efficiency_60s,
        notional_burst,
        bid_depth_change_1m,
        ask_depth_change_1m,
        minimum_close_distance_atr,
        minimum_flow,
        minimum_efficiency,
        minimum_notional_burst,
        minimum_depth_withdrawal,
        minimum_close_location,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if (
        atr <= 0.0
        or high < low
        or minimum_close_distance_atr < 0.0
        or minimum_flow < 0.0
        or minimum_efficiency < 0.0
        or minimum_notional_burst < 0.0
        or minimum_depth_withdrawal < 0.0
        or not 0.0 <= minimum_close_location <= 1.0
    ):
        return False

    outside_distance = continuation_side * (close - sweep_extreme) / atr
    span = max(high - low, 1e-12)
    close_location = (
        (close - low) / span
        if continuation_side > 0
        else (high - close) / span
    )
    relevant_depth_change = (
        ask_depth_change_1m
        if continuation_side > 0
        else bid_depth_change_1m
    )
    return (
        outside_distance >= minimum_close_distance_atr
        and continuation_side * flow_15s >= minimum_flow
        and continuation_side * flow_60s >= minimum_flow
        and efficiency_60s >= minimum_efficiency
        and notional_burst >= minimum_notional_burst
        and -relevant_depth_change >= minimum_depth_withdrawal
        and close_location >= minimum_close_location
        and continuation_side * (close - open_price) > 0.0
    )


def first_continuation_retest_response(
    *,
    continuation_side: int,
    sweep_extreme: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    maximum_counterflow: float,
    minimum_directional_depth: float = DIRECTIONAL_DEPTH_MIN,
) -> bool:
    """Return whether the first later touch defended the reaccepted extreme."""
    return first_accepted_level_retest_response(
        side=continuation_side,
        level=sweep_extreme,
        high=high,
        low=low,
        close=close,
        flow_15s=flow_15s,
        depth_imbalance=depth_imbalance,
        maximum_counterflow=maximum_counterflow,
        minimum_directional_depth=minimum_directional_depth,
    )


__all__ = [
    "continuation_reacceptance_ready",
    "first_continuation_retest_response",
]
