"""Pure state contract for a genuine no-retrace CHoCH breakaway.

This module classifies an already observed entry path only. It contains no
market replay, order, fill, position, PnL, NAV or risk-sizing behavior.
"""
from __future__ import annotations


def no_retrace_breakaway_allowed(
    *,
    retest_touch_count: int,
    breakaway_candidate: bool,
) -> bool:
    """Return whether a breakaway candidate is still semantically no-retrace.

    Once price has touched the frozen CHoCH reference, the auction is a retest
    episode. A later extension cannot be re-labelled as a no-retrace breakaway;
    it must first complete the independent retest-response confirmation path.
    """
    if retest_touch_count < 0:
        raise ValueError("retest_touch_count must be non-negative")
    return bool(breakaway_candidate) and retest_touch_count == 0


__all__ = ["no_retrace_breakaway_allowed"]
