"""Directional book-depth predicates for Candidate 05.

This module is observational only. It does not submit orders, match fills,
maintain positions, calculate fees, or construct NAV.
"""
from __future__ import annotations

import math


DIRECTIONAL_DEPTH_MIN = 0.10


def directional_depth_support(*, side: int, depth_imbalance: float, minimum: float = DIRECTIONAL_DEPTH_MIN) -> bool:
    """Whether resting depth is materially stronger in the proposed direction.

    ``depth_imbalance`` is (bid_depth - ask_depth) / total_depth. Long reversals
    require positive bid dominance; short reversals require negative ask
    dominance. A ten-percentage-point edge is intentionally symmetric and is
    treated as a state requirement, not a score or sizing multiplier.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not math.isfinite(depth_imbalance):
        return False
    if minimum < 0.0 or minimum >= 1.0:
        raise ValueError("minimum must be in [0, 1)")
    return side * depth_imbalance >= minimum
