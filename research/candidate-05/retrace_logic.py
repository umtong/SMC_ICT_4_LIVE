"""Pure causal geometry for Candidate 05 v2.

This module contains no execution, fill, accounting, position, or PnL engine.
It exists only to make the higher-timeframe aggregation and retrace-state
predicates independently testable.
"""
from __future__ import annotations

from typing import Mapping, Sequence


def displacement_retrace_level(sweep_extreme: float, confirmation_close: float) -> float:
    """Return the 50% retrace of a completed sweep-to-displacement impulse."""
    if sweep_extreme <= 0.0 or confirmation_close <= 0.0:
        raise ValueError("prices must be positive")
    return (sweep_extreme + confirmation_close) / 2.0


def structural_stop(sweep_extreme: float, side: int, atr: float, buffer_atr: float) -> float:
    """Place invalidation beyond the swept extreme, symmetrically by side."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if sweep_extreme <= 0.0 or atr <= 0.0 or buffer_atr < 0.0:
        raise ValueError("invalid stop inputs")
    return sweep_extreme - side * atr * buffer_atr


def pending_limit_invalidated(*, side: int, stop: float, high: float, low: float) -> bool:
    """Whether structural invalidation was reached before a resting fill."""
    if side == 1:
        return low <= stop
    if side == -1:
        return high >= stop
    raise ValueError("side must be -1 or 1")


def aggregate_completed_bar(rows: Sequence[Mapping[str, float | int]]) -> dict[str, float | int]:
    """Aggregate a non-empty completed-bar sequence without looking ahead."""
    if not rows:
        raise ValueError("rows must not be empty")
    return {
        "ts": int(rows[-1]["ts"]),
        "open": float(rows[0]["open"]),
        "high": max(float(row["high"]) for row in rows),
        "low": min(float(row["low"]) for row in rows),
        "close": float(rows[-1]["close"]),
        "volume": sum(float(row["volume"]) for row in rows),
    }
