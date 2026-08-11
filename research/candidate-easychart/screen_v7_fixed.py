#!/usr/bin/env python3
"""Unit-stable timestamp wrapper for the v7 session diagnostic.

Pandas can preserve Binance millisecond timestamps as ``datetime64[ms, UTC]``.
Casting such a series to ``int64`` returns milliseconds, while the strategy's
causal contracts use Unix nanoseconds.  Comparing timestamps as timestamps
avoids that unit-dependent mismatch without changing any trading rule.
"""
from __future__ import annotations

import pandas as pd

import screen_v7 as _base


def range_prices(
    frame: pd.DataFrame,
    start_ns: int,
    end_ns: int,
) -> tuple[float, float] | None:
    start = pd.Timestamp(start_ns, unit="ns", tz="UTC")
    end = pd.Timestamp(end_ns, unit="ns", tz="UTC")
    selected = frame[
        (frame["open_time_dt"] >= start)
        & (frame["open_time_dt"] < end)
    ]
    if selected.empty:
        return None
    high = float(selected["high"].max())
    low = float(selected["low"].min())
    if not high > low:
        return None
    return high, low


_base.range_prices = range_prices


if __name__ == "__main__":
    _base.main()
