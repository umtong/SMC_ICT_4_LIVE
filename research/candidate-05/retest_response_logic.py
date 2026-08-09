"""Pure causal predicates for a CHoCH retest-response entry."""
from __future__ import annotations

import math


def retest_touched(
    *,
    side: int,
    reference_price: float,
    high: float,
    low: float,
) -> bool:
    """Return whether a completed bar traded back to the CHoCH reference.

    The predicate is mirror symmetric. A long retest trades at or below the
    reference; a short retest trades at or above it. Touch alone is observation,
    not an entry signal.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (reference_price, high, low)
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if high < low:
        return False
    return low <= reference_price if side > 0 else high >= reference_price


def retest_response_ready(
    *,
    side: int,
    reference_price: float,
    high: float,
    low: float,
    close: float,
    flow_15s: float,
    depth_imbalance: float,
    minimum_directional_depth: float,
) -> bool:
    """Confirm that the actual retest was rejected with current sponsorship.

    A valid response requires the completed bar to touch the CHoCH reference,
    close back on the scenario side, finish with aligned final-15-second
    aggressor flow, and retain the existing directional-depth minimum. This
    deliberately checks the book and flow at the executable retest rather than
    reusing only the earlier sweep or CHoCH observations.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        reference_price,
        high,
        low,
        close,
        flow_15s,
        depth_imbalance,
        minimum_directional_depth,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if minimum_directional_depth < 0.0:
        return False
    return (
        retest_touched(
            side=side,
            reference_price=reference_price,
            high=high,
            low=low,
        )
        and side * (close - reference_price) > 0.0
        and side * flow_15s > 0.0
        and side * depth_imbalance >= minimum_directional_depth
    )


__all__ = ["retest_response_ready", "retest_touched"]
