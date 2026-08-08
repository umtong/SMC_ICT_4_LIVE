"""Tick-aware terminal outcome classification for Nautilus position events."""

from __future__ import annotations


def classify_position_outcome(
    *,
    direction: str,
    close_price: float,
    target_price: float,
    stop_price: float,
    tick: float,
    forced_exit_reason: str | None = None,
) -> str:
    """Classify a native close without floating-point boundary drift.

    Nautilus can report a one-tick-slipped target as values such as
    ``64107.80000000001``.  Comparing that binary float directly with the
    mathematically equivalent ``target + tick`` mislabels a target fill as an
    unrelated exit.  The tolerance here is only a tiny fraction of one tick;
    it cannot turn a genuinely different market price into a target or stop.
    """

    if forced_exit_reason:
        return str(forced_exit_reason)
    if tick <= 0.0:
        raise ValueError("tick must be positive")

    epsilon = max(abs(tick) * 1e-9, 1e-12)
    normalized = str(direction).upper()
    if normalized == "LONG":
        if close_price >= target_price - tick - epsilon:
            return "TARGET"
        if close_price <= stop_price + tick + epsilon:
            return "STOP"
    elif normalized == "SHORT":
        if close_price <= target_price + tick + epsilon:
            return "TARGET"
        if close_price >= stop_price - tick - epsilon:
            return "STOP"
    else:
        raise ValueError(f"unsupported direction: {direction!r}")
    return "OTHER_EXIT"
