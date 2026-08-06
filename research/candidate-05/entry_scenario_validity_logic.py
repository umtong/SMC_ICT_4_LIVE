"""Pure lifecycle resolution for a scenario-valid resting entry."""
from __future__ import annotations

import math


STRUCTURAL_STOP = "STRUCTURAL_STOP_REACHED_WHILE_ENTRY_RESTING"
TARGET_COMPLETED = "SCENARIO_TARGET_REACHED_WHILE_ENTRY_RESTING"
TARGET_SOURCE_EXPIRED = "SCENARIO_TARGET_SOURCE_EXPIRED_WHILE_ENTRY_RESTING"
DAYTRADE_HORIZON_EXPIRED = "SCENARIO_DAYTRADE_HORIZON_EXPIRED_WHILE_ENTRY_RESTING"
FUNDING_BOUNDARY = "FUNDING_BLACKOUT_WHILE_ENTRY_RESTING"
EVALUATION_BOUNDARY = "EVALUATION_ENDED_WHILE_ENTRY_RESTING"


def scenario_entry_cancel_reason(
    *,
    side: int,
    high: float,
    low: float,
    structural_stop: float,
    target: float,
    target_source_active: bool,
    bar_index: int,
    horizon_index: int,
    funding_blackout: bool,
    in_evaluation: bool,
) -> str | None:
    """Resolve a resting entry from scenario state, never an arbitrary bar count.

    The original structural stop and destination are evaluated before time
    boundaries.  This intentionally has no fixed two- or eight-bar expiry: an
    executable intent remains coherent until the auction is invalidated,
    completes without a fill, loses its live destination, or reaches an existing
    daytrade boundary.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (high, low, structural_stop, target)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("price inputs must be finite")
    if high < low:
        raise ValueError("high must be greater than or equal to low")

    stop_reached = low <= structural_stop if side > 0 else high >= structural_stop
    if stop_reached:
        return STRUCTURAL_STOP

    target_reached = high >= target if side > 0 else low <= target
    if target_reached:
        return TARGET_COMPLETED
    if not target_source_active:
        return TARGET_SOURCE_EXPIRED
    if bar_index >= horizon_index:
        return DAYTRADE_HORIZON_EXPIRED
    if funding_blackout:
        return FUNDING_BOUNDARY
    if not in_evaluation:
        return EVALUATION_BOUNDARY
    return None


__all__ = [
    "DAYTRADE_HORIZON_EXPIRED",
    "EVALUATION_BOUNDARY",
    "FUNDING_BOUNDARY",
    "STRUCTURAL_STOP",
    "TARGET_COMPLETED",
    "TARGET_SOURCE_EXPIRED",
    "scenario_entry_cancel_reason",
]
