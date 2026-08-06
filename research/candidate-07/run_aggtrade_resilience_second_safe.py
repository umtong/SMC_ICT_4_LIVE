#!/usr/bin/env python3
"""Run aggregate-trade resilience with exact completed-second causality.

Binance minute/five-minute bars expose a close time ending in ``...999 ms``.
The aggregate-trade reduction represents the same wall-clock second at
``...999999999 ns``.  Comparing those timestamps numerically would incorrectly
permit the final second used to confirm a pool to become its first post-
confirmation contact.  This wrapper changes only that timestamp boundary: a
pool becomes touchable at the *next* whole second.  All market logic, thresholds,
geometry and the frozen Week-1 remain unchanged.
"""
from __future__ import annotations

import numpy as np

import diagnose_impact_resilience_1s as impact


def first_touch_after_complete_confirmation_second(
    pool: impact.Pool,
    *,
    timestamps: np.ndarray,
    previous_close: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    stop_index: int | None = None,
) -> int | None:
    """Return literal first touch strictly after the confirmation second."""
    confirmation_second = int(pool.confirmed_ts_ns) // impact.NS_PER_SECOND
    first_eligible_end_ns = (
        (confirmation_second + 1) * impact.NS_PER_SECOND
        + impact.NS_PER_SECOND
        - 1
    )
    start = int(np.searchsorted(timestamps, first_eligible_end_ns, side="left"))
    end = len(timestamps) if stop_index is None else min(len(timestamps), stop_index + 1)
    if start >= end:
        return None
    if pool.side == "UPPER":
        mask = (
            (previous_close[start:end] <= pool.level)
            & (highs[start:end] >= pool.level)
        )
    else:
        mask = (
            (previous_close[start:end] >= pool.level)
            & (lows[start:end] <= pool.level)
        )
    hits = np.flatnonzero(mask)
    return None if len(hits) == 0 else start + int(hits[0])


# Both source contact selection and target-consumption checks resolve this
# module attribute at call time.  Patching before importing the wrapper keeps
# one explicit implementation correction without copying strategy logic.
impact._first_touch_index = first_touch_after_complete_confirmation_second

from diagnose_aggtrade_resilience_v2 import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
