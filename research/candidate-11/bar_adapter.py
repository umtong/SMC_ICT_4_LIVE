"""Causal DataFrame-to-Nautilus ``Bar`` adapter for Candidate 11.

This only materializes official Nautilus model objects. It does not implement a
clock, matcher, fill model, fee model, position ledger, or NAV calculation.
Those remain exclusively inside NautilusTrader.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

_BAR_COLUMNS = ("open", "high", "low", "close", "volume")


def build_bars(frame: pd.DataFrame, bar_type: Any, instrument: Any) -> list[Any]:
    """Build timestamp-ordered Nautilus bars from completed causal observations."""
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.objects import Price, Quantity

    missing = [name for name in _BAR_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"missing OHLCV columns: {missing}")
    if frame.empty:
        raise ValueError("bar frame is empty")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError("bar frame index must be a DatetimeIndex")
    if frame.index.tz is None:
        raise ValueError("bar frame timestamps must be timezone-aware")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("bar frame timestamps must be strictly increasing")

    matrix = frame.loc[:, _BAR_COLUMNS].to_numpy(dtype="float64", copy=True)
    if not pd.notna(matrix).all():
        raise ValueError("bar frame contains non-finite OHLCV values")

    price_format = f".{int(instrument.price_precision)}f"
    size_format = f".{int(instrument.size_precision)}f"
    bars: list[Any] = []
    for timestamp, values in zip(frame.index, matrix, strict=True):
        open_, high, low, close, volume = (float(value) for value in values)
        if high < max(open_, close, low) or low > min(open_, close, high):
            raise ValueError(f"inconsistent OHLC at {timestamp.isoformat()}")
        if volume < 0:
            raise ValueError(f"negative volume at {timestamp.isoformat()}")
        ts_ns = int(timestamp.value)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(format(open_, price_format)),
                high=Price.from_str(format(high, price_format)),
                low=Price.from_str(format(low, price_format)),
                close=Price.from_str(format(close, price_format)),
                volume=Quantity.from_str(format(volume, size_format)),
                ts_event=ts_ns,
                ts_init=ts_ns,
            ),
        )
    return bars
