"""Pure predicates for a failed-impact, recovered-book reversal.

No execution, fill, fee, position, margin or NAV logic belongs here.  These
functions only interpret completed observations.  NautilusTrader remains the
sole execution and accounting engine.
"""
from __future__ import annotations

import math

from depth_logic import DIRECTIONAL_DEPTH_MIN


def impact_failure_ready(
    *,
    shock_side: int,
    external_level: float,
    shock_high: float,
    shock_low: float,
    close: float,
    flow_15s: float,
    efficiency_60s: float,
    bid_depth_change_1m: float,
    ask_depth_change_1m: float,
    maximum_efficiency: float,
    minimum_reversal_flow: float,
    minimum_depth_refill: float,
) -> bool:
    """Whether a prior accepted external shock was subsequently absorbed.

    `shock_side` is the direction of the original external break.  The later
    completed bar must close back through the broken level and the shock
    midpoint, exhibit low marginal price efficiency, show final-tail flow in the
    reversal direction, and replenish the side of the book depleted by the
    shock.  Definitions are exact long/short mirrors.
    """
    if shock_side not in (-1, 1):
        raise ValueError("shock_side must be -1 or 1")
    values = (
        external_level,
        shock_high,
        shock_low,
        close,
        flow_15s,
        efficiency_60s,
        bid_depth_change_1m,
        ask_depth_change_1m,
        maximum_efficiency,
        minimum_reversal_flow,
        minimum_depth_refill,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if shock_high <= shock_low:
        return False
    if maximum_efficiency < 0.0 or minimum_reversal_flow < 0.0 or minimum_depth_refill < 0.0:
        return False

    midpoint = (shock_high + shock_low) / 2.0
    if shock_side > 0:
        reclaimed = close < min(external_level, midpoint)
        depleted_side_refilled = ask_depth_change_1m >= minimum_depth_refill
    else:
        reclaimed = close > max(external_level, midpoint)
        depleted_side_refilled = bid_depth_change_1m >= minimum_depth_refill
    reversal_tail_flow = -shock_side * flow_15s
    return (
        reclaimed
        and efficiency_60s <= maximum_efficiency
        and reversal_tail_flow >= minimum_reversal_flow
        and depleted_side_refilled
    )


def failed_break_reaccepted(
    *,
    shock_side: int,
    external_level: float,
    close: float,
) -> bool:
    """Whether price closed outside again in the original shock direction."""
    if shock_side not in (-1, 1):
        raise ValueError("shock_side must be -1 or 1")
    if not math.isfinite(external_level) or not math.isfinite(close):
        return True
    return close > external_level if shock_side > 0 else close < external_level


def first_failed_break_retest_response(
    *,
    trade_side: int,
    external_level: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    maximum_counterflow: float,
    minimum_directional_depth: float = DIRECTIONAL_DEPTH_MIN,
) -> bool:
    """Whether the first later return to the failed level was defended.

    Entry may only be submitted after this completed bar.  The bar must touch
    the level, close on the reversal side, avoid excessive counterflow and show
    current resting depth aligned with the proposed trade.
    """
    if trade_side not in (-1, 1):
        raise ValueError("trade_side must be -1 or 1")
    values = (
        external_level,
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
    if high < low or maximum_counterflow < 0.0 or minimum_directional_depth < 0.0:
        return False
    touched = low <= external_level <= high
    defended = close > external_level if trade_side > 0 else close < external_level
    return (
        touched
        and defended
        and trade_side * flow_15s >= -maximum_counterflow
        and trade_side * depth_imbalance >= minimum_directional_depth
    )


def failed_break_structural_stop(
    *,
    trade_side: int,
    shock_high: float,
    shock_low: float,
    atr: float,
    stop_buffer_atr: float,
) -> float:
    """Place invalidation beyond the original shock extreme."""
    if trade_side not in (-1, 1):
        raise ValueError("trade_side must be -1 or 1")
    values = (shock_high, shock_low, atr, stop_buffer_atr)
    if not all(math.isfinite(float(value)) for value in values):
        return float("nan")
    if shock_high <= shock_low or atr <= 0.0 or stop_buffer_atr < 0.0:
        return float("nan")
    buffer = atr * stop_buffer_atr
    return shock_low - buffer if trade_side > 0 else shock_high + buffer


__all__ = [
    "failed_break_reaccepted",
    "failed_break_structural_stop",
    "first_failed_break_retest_response",
    "impact_failure_ready",
]
