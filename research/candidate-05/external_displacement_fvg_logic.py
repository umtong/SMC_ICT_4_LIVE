"""Pure mirror-symmetric predicates for external-liquidity displacement FVGs.

This module contains no order matching, fills, positions, accounting or NAV
logic.  It defines only completed-bar geometry and current response predicates;
NautilusTrader remains the sole execution and accounting engine.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from depth_logic import DIRECTIONAL_DEPTH_MIN


@dataclass(frozen=True, slots=True)
class DisplacementGap:
    """A completed three-bar directional price imbalance."""

    side: int
    lower: float
    upper: float
    midpoint: float


def displacement_gap(
    *,
    side: int,
    first_high: float,
    first_low: float,
    impulse_open: float,
    impulse_high: float,
    impulse_low: float,
    impulse_close: float,
    third_high: float,
    third_low: float,
    atr: float,
    minimum_body_atr: float,
    minimum_close_location: float,
) -> DisplacementGap | None:
    """Return a directional FVG only after a completed displacement sequence.

    For a bullish gap the third bar's low must be above the first bar's high;
    the bearish definition is its exact mirror.  The middle bar must be a
    directionally large body and close near its directional extreme.  Thresholds
    are supplied by the existing Candidate 05 displacement contract rather than
    fitted here.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        first_high,
        first_low,
        impulse_open,
        impulse_high,
        impulse_low,
        impulse_close,
        third_high,
        third_low,
        atr,
        minimum_body_atr,
        minimum_close_location,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return None
    if atr <= 0.0:
        return None
    span = impulse_high - impulse_low
    if span <= 0.0:
        return None
    body_atr = side * (impulse_close - impulse_open) / atr
    close_location = (
        (impulse_close - impulse_low) / span
        if side > 0
        else (impulse_high - impulse_close) / span
    )
    if body_atr < minimum_body_atr or close_location < minimum_close_location:
        return None

    if side > 0:
        lower = first_high
        upper = third_low
        if upper <= lower:
            return None
    else:
        lower = third_high
        upper = first_low
        if upper <= lower:
            return None
    return DisplacementGap(
        side=side,
        lower=lower,
        upper=upper,
        midpoint=(lower + upper) / 2.0,
    )


def gap_invalidated(
    *,
    side: int,
    external_level: float,
    gap: DisplacementGap,
    close: float,
) -> bool:
    """Whether price accepted back through both imbalance and external break."""
    if side not in (-1, 1) or gap.side != side:
        raise ValueError("gap side must match side")
    values = (external_level, gap.lower, gap.upper, close)
    if not all(math.isfinite(float(value)) for value in values):
        return True
    if side > 0:
        return close <= min(external_level, gap.lower)
    return close >= max(external_level, gap.upper)


def first_retest_response(
    *,
    side: int,
    external_level: float,
    gap: DisplacementGap,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    maximum_counterflow: float,
    minimum_directional_depth: float = DIRECTIONAL_DEPTH_MIN,
) -> bool:
    """Whether the first return into the gap was defended in breakout direction.

    The completed retest must touch the proximal half of the imbalance, close
    back on the favorable side of its midpoint while preserving the external
    break, avoid excessive final-15-second counterflow, and show current resting
    depth in the same direction.  A limit may only be submitted after this bar,
    so no same-bar retrospective fill is possible.
    """
    if side not in (-1, 1) or gap.side != side:
        raise ValueError("gap side must match side")
    values = (
        external_level,
        gap.lower,
        gap.upper,
        gap.midpoint,
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

    if side > 0:
        touched = low <= gap.midpoint and high >= gap.lower
        defended = close >= gap.midpoint and close > external_level
    else:
        touched = high >= gap.midpoint and low <= gap.upper
        defended = close <= gap.midpoint and close < external_level
    directional_tail_flow = side * flow_15s
    directional_depth = side * depth_imbalance
    return (
        touched
        and defended
        and directional_tail_flow >= -maximum_counterflow
        and directional_depth >= minimum_directional_depth
    )


def structural_gap_stop(
    *,
    side: int,
    external_level: float,
    gap: DisplacementGap,
    atr: float,
    stop_buffer_atr: float,
) -> float:
    """Place invalidation beyond both the accepted level and distal gap edge."""
    if side not in (-1, 1) or gap.side != side:
        raise ValueError("gap side must match side")
    values = (external_level, gap.lower, gap.upper, atr, stop_buffer_atr)
    if not all(math.isfinite(float(value)) for value in values):
        return float("nan")
    if atr <= 0.0 or stop_buffer_atr < 0.0:
        return float("nan")
    buffer = atr * stop_buffer_atr
    if side > 0:
        return min(external_level, gap.lower) - buffer
    return max(external_level, gap.upper) + buffer


__all__ = [
    "DisplacementGap",
    "displacement_gap",
    "first_retest_response",
    "gap_invalidated",
    "structural_gap_stop",
]
