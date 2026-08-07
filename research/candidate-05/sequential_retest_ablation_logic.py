"""One-variable ablation of current displayed depth at v35 first retest."""
from __future__ import annotations

import math


def first_sequential_boundary_retest_without_depth(
    *,
    side: int,
    boundary: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    maximum_counterflow: float,
) -> bool:
    """Preserve first touch, close-side defence and current tail flow only."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (boundary, high, low, close, flow_15s, maximum_counterflow)
    if not all(math.isfinite(float(value)) for value in values):
        return False
    touched = low <= boundary if side > 0 else high >= boundary
    defended = close > boundary if side > 0 else close < boundary
    return touched and defended and side * flow_15s >= -maximum_counterflow


__all__ = ["first_sequential_boundary_retest_without_depth"]
