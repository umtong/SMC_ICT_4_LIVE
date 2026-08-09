"""Pure state predicates for preserving a scenario's CHoCH-time destination."""
from __future__ import annotations

import math


def frozen_target_reached_before_entry(
    *,
    side: int,
    target: float,
    high: float,
    low: float,
) -> bool:
    """Whether the scenario completed its expected move before an entry existed.

    A long target is reached by the completed bar high; a short target by its
    low. Once this happens the original auction opportunity is complete and a
    later retrace must not silently receive a newly selected, farther target.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not all(math.isfinite(float(value)) for value in (target, high, low)):
        return False
    if high < low:
        return False
    return high >= target if side > 0 else low <= target


def revalidate_frozen_milestone(
    *,
    side: int,
    entry: float,
    target: float,
    milestone: float,
    atr: float,
    stop_buffer_atr: float,
    cost_rate: float,
    adverse_slippage_rate: float,
) -> tuple[float, float] | None:
    """Revalidate, but never replace, a CHoCH-time protection milestone.

    Returns ``(protected_stop, expected_net_per_unit)`` only when the original
    milestone is still strictly between the executable entry and frozen final
    target, and its structurally buffered exit remains positive after the same
    fees and adverse exit slippage used by the strategy. A later-created pool is
    never substituted here.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    values = (
        entry,
        target,
        milestone,
        atr,
        stop_buffer_atr,
        cost_rate,
        adverse_slippage_rate,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return None
    if (
        entry <= 0.0
        or target <= 0.0
        or milestone <= 0.0
        or atr <= 0.0
        or stop_buffer_atr < 0.0
        or cost_rate < 0.0
        or adverse_slippage_rate < 0.0
    ):
        return None

    between = entry < milestone < target if side > 0 else target < milestone < entry
    if not between:
        return None
    protected_stop = milestone - side * stop_buffer_atr * atr
    expected_exit = protected_stop * (1.0 - side * adverse_slippage_rate)
    expected_net = side * (expected_exit - entry) - cost_rate * (entry + expected_exit)
    if expected_net <= 0.0:
        return None
    return protected_stop, expected_net


__all__ = [
    "frozen_target_reached_before_entry",
    "revalidate_frozen_milestone",
]
