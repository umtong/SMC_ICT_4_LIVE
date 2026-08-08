"""Pure causal phase predicate for Candidate 05 first-retrace entries."""
from __future__ import annotations

import math


TWO_TO_ONE_IMBALANCE = 1.0 / 3.0


def early_reversal_transfer(
    *,
    side: int,
    flow_15s: float,
    flow_60s: float,
    flow_3m: float,
) -> bool:
    """Whether local reversal flow leads rather than trails the broad auction.

    A first retrace is an early-transfer entry. It is coherent only while the
    completed 15-second tail is favorable and ahead of the completed three-
    minute flow. Once the broad three-minute imbalance itself reaches a two-to-
    one state, the move is treated as mature rather than as a fresh transfer.
    No order, fill, PnL or future observation is used here.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not all(math.isfinite(float(value)) for value in (flow_15s, flow_60s, flow_3m)):
        return False
    tail = side * float(flow_15s)
    minute = side * float(flow_60s)
    broad = side * float(flow_3m)
    return (
        tail > 0.0
        and tail > broad
        and broad < TWO_TO_ONE_IMBALANCE
        and minute < TWO_TO_ONE_IMBALANCE
    )


__all__ = ["TWO_TO_ONE_IMBALANCE", "early_reversal_transfer"]
