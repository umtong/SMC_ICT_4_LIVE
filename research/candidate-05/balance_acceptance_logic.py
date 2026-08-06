"""Pure predicates for position-building balance acceptance.

These functions only classify completed observations. They never match orders,
maintain positions, or compute PnL.
"""
from __future__ import annotations

import math


BALANCE_ACCEPTANCE_HOLD_BARS = 3


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def depth_migration_sponsors_acceptance(
    *,
    side: int,
    depth_imbalance: float,
    bid_depth_change_5m: float,
    ask_depth_change_5m: float,
    minimum_directional_depth: float,
) -> bool:
    """Require depth to migrate with the expansion, not merely disappear.

    For a long, bids must replenish while asks withdraw. For a short, asks must
    replenish while bids withdraw. The book imbalance must also meet the same
    directional minimum already used by Candidate 05 reversals. This separates
    position-building acceptance from a one-sided liquidation vacuum.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(
        depth_imbalance,
        bid_depth_change_5m,
        ask_depth_change_5m,
        minimum_directional_depth,
    ):
        return False
    same_side_change = bid_depth_change_5m if side > 0 else ask_depth_change_5m
    opposing_change = ask_depth_change_5m if side > 0 else bid_depth_change_5m
    return (
        side * depth_imbalance >= minimum_directional_depth
        and same_side_change > 0.0
        and opposing_change < 0.0
    )


def closes_outside_balance(
    *,
    side: int,
    close: float,
    balance_high: float,
    balance_low: float,
) -> bool:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(close, balance_high, balance_low) or balance_high <= balance_low:
        return False
    return close > balance_high if side > 0 else close < balance_low


def balance_retest_confirms_acceptance(
    *,
    side: int,
    high: float,
    low: float,
    close: float,
    balance_high: float,
    balance_low: float,
    atr: float,
    flow_15s: float,
    depth_imbalance: float,
    retrace_tolerance_atr: float,
    minimum_close_location: float,
    minimum_directional_depth: float,
) -> bool:
    """Confirm the first retest remained accepted outside the old balance."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(
        high,
        low,
        close,
        balance_high,
        balance_low,
        atr,
        flow_15s,
        depth_imbalance,
        retrace_tolerance_atr,
        minimum_close_location,
        minimum_directional_depth,
    ) or atr <= 0.0 or high < low or balance_high <= balance_low:
        return False

    boundary = balance_high if side > 0 else balance_low
    touched = (
        low <= boundary + retrace_tolerance_atr * atr
        if side > 0
        else high >= boundary - retrace_tolerance_atr * atr
    )
    if not touched or not closes_outside_balance(
        side=side,
        close=close,
        balance_high=balance_high,
        balance_low=balance_low,
    ):
        return False

    span = max(high - low, 1e-12)
    close_location = (
        (close - low) / span
        if side > 0
        else (high - close) / span
    )
    return (
        close_location >= minimum_close_location
        and side * flow_15s > 0.0
        and side * depth_imbalance >= minimum_directional_depth
    )


__all__ = [
    "BALANCE_ACCEPTANCE_HOLD_BARS",
    "balance_retest_confirms_acceptance",
    "closes_outside_balance",
    "depth_migration_sponsors_acceptance",
]
