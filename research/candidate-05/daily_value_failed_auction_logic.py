"""Pure causal predicates for a daily value failed-auction scenario.

A completed UTC day supplies three market-generated references: its high, low
and traded-value VWAP.  The next day may trade a boundary failure only when one
side is materially raided and the completed bar closes back inside.  Order-flow
sponsorship remains the responsibility of the existing strict inventory-transfer
predicate; this module only owns the daily auction geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from inventory_repricing_logic import EXTERNAL_PENETRATION_ATR_MIN


@dataclass(frozen=True, slots=True)
class CompletedDailyValue:
    day: str
    high: float
    low: float
    vwap: float

    def __post_init__(self) -> None:
        if not self.day:
            raise ValueError("day must be non-empty")
        if not all(math.isfinite(float(value)) for value in (self.high, self.low, self.vwap)):
            raise ValueError("daily value inputs must be finite")
        if self.low <= 0.0 or self.high <= self.low:
            raise ValueError("daily range must be positive")
        if not self.low <= self.vwap <= self.high:
            raise ValueError("daily VWAP must lie inside the completed range")


def failed_auction_side(
    *,
    previous_close: float,
    high: float,
    low: float,
    close: float,
    reference: CompletedDailyValue,
    atr: float,
) -> int:
    """Return +1 after a failed low auction, -1 after a failed high auction."""
    values = (previous_close, high, low, close, atr)
    if not all(math.isfinite(float(value)) for value in values) or atr <= 0.0:
        return 0
    high_failure = (
        previous_close <= reference.high
        and high >= reference.high + EXTERNAL_PENETRATION_ATR_MIN * atr
        and close < reference.high
    )
    low_failure = (
        previous_close >= reference.low
        and low <= reference.low - EXTERNAL_PENETRATION_ATR_MIN * atr
        and close > reference.low
    )
    if high_failure == low_failure:
        return 0
    return -1 if high_failure else 1


def daily_value_target_candidates(
    *,
    side: int,
    entry: float,
    reference: CompletedDailyValue,
) -> tuple[tuple[str, float], ...]:
    """Return natural accepted-value objectives nearest-first.

    VWAP is the first destination because it represents the prior day's accepted
    traded value.  The opposite daily extreme is the secondary external
    liquidity objective.  Invalid or already-consumed destinations are omitted.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not math.isfinite(float(entry)) or entry <= 0.0:
        return ()
    raw = (
        (f"PREVIOUS_DAY_VWAP:{reference.day}", reference.vwap),
        (
            f"PREVIOUS_DAY_{'HIGH' if side > 0 else 'LOW'}:{reference.day}",
            reference.high if side > 0 else reference.low,
        ),
    )
    return tuple(
        (source, float(price))
        for source, price in raw
        if side * (float(price) - float(entry)) > 0.0
    )


__all__ = [
    "CompletedDailyValue",
    "daily_value_target_candidates",
    "failed_auction_side",
]
