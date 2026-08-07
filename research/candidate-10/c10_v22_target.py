"""External-liquidity target hierarchy for candidate 10 v22.

The v21 detector and entry grammar are unchanged. This module changes only the
structural target selected after a valid liquidation-auction confirmation:
internal five-minute pivot pools may trigger a scenario, but only an older,
unconsumed eight-hour funding-session boundary may serve as the executable
profit target.
"""
from __future__ import annotations

from typing import Iterable, Protocol, TypeVar


class PoolLike(Protocol):
    pool_id: str
    side: str
    price: float
    source: str
    consumed: bool
    reserved: bool


PoolT = TypeVar("PoolT", bound=PoolLike)


def select_external_target(
    pools: Iterable[PoolT],
    *,
    direction: int,
    entry: float,
    source_pool_id: str,
) -> PoolT | None:
    """Return the nearest directionally valid external session pool.

    This is deliberately not a score or optimization rule. It implements the
    dealing-range hierarchy being tested: confirmed pivots are internal
    liquidity, while completed eight-hour session extremes are external
    liquidity. Consumed, reserved and source pools are never reused.
    """

    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or +1")
    candidates = [
        pool
        for pool in pools
        if not pool.consumed
        and not pool.reserved
        and pool.pool_id != source_pool_id
        and pool.source == "FUNDING_SESSION"
        and (
            (direction > 0 and pool.side == "HIGH" and pool.price > entry)
            or (direction < 0 and pool.side == "LOW" and pool.price < entry)
        )
    ]
    if direction > 0:
        return min(candidates, key=lambda item: item.price, default=None)
    return max(candidates, key=lambda item: item.price, default=None)
