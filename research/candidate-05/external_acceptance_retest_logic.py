"""Pure predicates for a first defended retest of an accepted external level."""
from __future__ import annotations

import math

from depth_logic import DIRECTIONAL_DEPTH_MIN


def accepted_level_invalidated(*, side: int, level: float, close: float) -> bool:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not math.isfinite(level) or not math.isfinite(close):
        return True
    return close <= level if side > 0 else close >= level


def first_accepted_level_retest_response(
    *,
    side: int,
    level: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    maximum_counterflow: float,
    minimum_directional_depth: float = DIRECTIONAL_DEPTH_MIN,
) -> bool:
    """Require a completed touch and directional defense of the accepted level."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        level,
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
    touched = low <= level <= high
    defended = close > level if side > 0 else close < level
    return (
        touched
        and defended
        and side * flow_15s >= -maximum_counterflow
        and side * depth_imbalance >= minimum_directional_depth
    )


def external_level_structural_stop(
    *,
    side: int,
    level: float,
    atr: float,
    stop_buffer_atr: float,
) -> float:
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (level, atr, stop_buffer_atr)
    if not all(math.isfinite(float(value)) for value in values):
        return float("nan")
    if atr <= 0.0 or stop_buffer_atr < 0.0:
        return float("nan")
    return level - side * atr * stop_buffer_atr


__all__ = [
    "accepted_level_invalidated",
    "external_level_structural_stop",
    "first_accepted_level_retest_response",
]
