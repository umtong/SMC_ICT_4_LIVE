"""Breakaway depth-state predicates for Candidate 05.

The functions describe market state only. They do not submit or match orders,
manage positions, calculate fees, or construct NAV.
"""
from __future__ import annotations

import math


BREAKAWAY_FAVORABLE_DEPTH_RATIO = 2.0


def favorable_depth_ratio(*, side: int, depth_imbalance: float) -> float:
    """Return favorable resting depth divided by adverse resting depth.

    ``depth_imbalance`` is ``(bid - ask) / (bid + ask)``. For a long reversal,
    bid depth is favorable; for a short reversal, ask depth is favorable. The
    transformation is mirror symmetric and maps directional imbalance of 1/3
    to a two-to-one favorable-depth ratio.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not math.isfinite(depth_imbalance):
        return 0.0
    directional = side * depth_imbalance
    if directional >= 1.0:
        return math.inf
    if directional <= -1.0:
        return 0.0
    return (1.0 + directional) / (1.0 - directional)


def breakaway_depth_state(
    *,
    side: int,
    depth_imbalance: float,
    minimum_ratio: float = BREAKAWAY_FAVORABLE_DEPTH_RATIO,
) -> bool:
    """Whether resting liquidity is strong enough to expect no deep retrace.

    Compare in imbalance space rather than after the ratio transform so the
    exact two-to-one boundary (directional imbalance == 1/3) remains stable
    across Python/platform floating-point implementations.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if minimum_ratio <= 1.0 or not math.isfinite(minimum_ratio):
        raise ValueError("minimum_ratio must be finite and greater than one")
    if not math.isfinite(depth_imbalance):
        return False
    directional = side * depth_imbalance
    minimum_directional = (minimum_ratio - 1.0) / (minimum_ratio + 1.0)
    return directional >= minimum_directional or math.isclose(
        directional,
        minimum_directional,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )
