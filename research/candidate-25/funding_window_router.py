"""Pure causal decisions for quarter-hour reset continuation.

The router contains no orders, fills, accounting or PnL.  A seed can only be
created from the first ten seconds of the 07:45, 15:45 or 23:45 UTC minute after
that complete one-minute bar is observed.  The original imbalance is not an
entry.  Exactly thirty later completed bars must first move against that
imbalance.  The counter-move creates a fresh entry geometry for the medium-
horizon continuation which begins after the intervening funding settlement.
"""
from __future__ import annotations

import math


FUNDING_WINDOW_SIGNAL_HOURS = (7, 15, 23)
FUNDING_WINDOW_SIGNAL_MINUTE = 45


def seed_side(*, flow_open_10s: float, opening_participation_burst: float) -> int:
    """Return the imbalance direction only for above-baseline participation."""
    if not math.isfinite(flow_open_10s) or not math.isfinite(opening_participation_burst):
        return 0
    if opening_participation_burst <= 1.0 or flow_open_10s == 0.0:
        return 0
    return 1 if flow_open_10s > 0.0 else -1


def is_funding_window_seed_time(*, hour: int, minute: int) -> bool:
    return hour in FUNDING_WINDOW_SIGNAL_HOURS and minute == FUNDING_WINDOW_SIGNAL_MINUTE


def reset_confirmed(*, side: int, seed_close: float, reset_close: float) -> bool:
    """Require a strictly adverse first thirty-minute move before continuation."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or +1")
    if not math.isfinite(seed_close) or not math.isfinite(reset_close):
        return False
    return side * (reset_close - seed_close) < 0.0


__all__ = [
    "FUNDING_WINDOW_SIGNAL_HOURS",
    "FUNDING_WINDOW_SIGNAL_MINUTE",
    "is_funding_window_seed_time",
    "reset_confirmed",
    "seed_side",
]
