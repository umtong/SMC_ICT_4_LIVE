"""Pure state predicates for failed inventory traps becoming true acceptance.

The state is not a generic breakout.  It starts only after a strict external
inventory-transfer reversal setup has failed before CHoCH.  A continuation is
eligible only when price, aggressor flow, price efficiency and withdrawal of the
liquidity ahead all agree that the failed sweep extreme has become accepted.
"""
from __future__ import annotations

import math


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def failed_inventory_acceptance_ready(
    *,
    side: int,
    level: float,
    open_price: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    flow_15s: float,
    flow_60s: float,
    efficiency_60s: float,
    bid_depth_change_1m: float,
    ask_depth_change_1m: float,
    minimum_close_atr: float,
    minimum_flow: float,
    minimum_efficiency: float,
    minimum_depth_withdrawal: float,
    minimum_close_location: float,
) -> bool:
    """Whether a failed reversal has causally transitioned to acceptance."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        level,
        open_price,
        high,
        low,
        close,
        atr,
        flow_15s,
        flow_60s,
        efficiency_60s,
        bid_depth_change_1m,
        ask_depth_change_1m,
        minimum_close_atr,
        minimum_flow,
        minimum_efficiency,
        minimum_depth_withdrawal,
        minimum_close_location,
    )
    if not _finite(*values) or atr <= 0.0 or high < low:
        return False
    if any(
        value < 0.0
        for value in (
            minimum_close_atr,
            minimum_flow,
            minimum_efficiency,
            minimum_depth_withdrawal,
            minimum_close_location,
        )
    ):
        return False

    accepted_distance = side * (close - level) / atr
    directional_body = side * (close - open_price) / atr
    span = max(high - low, 1e-12)
    close_location = (close - low) / span if side > 0 else (high - close) / span
    # A long continuation advances through asks; a short advances through bids.
    liquidity_ahead_change = ask_depth_change_1m if side > 0 else bid_depth_change_1m
    withdrawal_ahead = -liquidity_ahead_change

    return (
        accepted_distance >= minimum_close_atr
        and directional_body > 0.0
        and min(side * flow_15s, side * flow_60s) >= minimum_flow
        and efficiency_60s >= minimum_efficiency
        and withdrawal_ahead >= minimum_depth_withdrawal
        and close_location >= minimum_close_location
    )


def accepted_level_retest_touched(
    *,
    side: int,
    level: float,
    high: float,
    low: float,
) -> bool:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(level, high, low) or high < low:
        return False
    return low <= level <= high


def accepted_level_closed_back_inside(*, side: int, level: float, close: float) -> bool:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(level, close):
        return True
    return close <= level if side > 0 else close >= level


__all__ = [
    "accepted_level_closed_back_inside",
    "accepted_level_retest_touched",
    "failed_inventory_acceptance_ready",
]
