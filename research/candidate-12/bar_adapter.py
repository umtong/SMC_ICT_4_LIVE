"""Causal completed-bar adapter into official NautilusTrader model objects."""
from __future__ import annotations

from typing import Any

import pandas as pd

_COLUMNS = ("open", "high", "low", "close", "volume")


def build_bars(frame: pd.DataFrame, bar_type: Any, instrument: Any) -> list[Any]:
    """Build strictly ordered Nautilus bars with close-time timestamps."""
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.objects import Price, Quantity

    missing = [name for name in _COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"missing OHLCV columns: {missing}")
    if frame.empty:
        raise ValueError("bar frame is empty")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError("bar frame index must be a DatetimeIndex")
    if frame.index.tz is None:
        raise ValueError("bar timestamps must be timezone-aware")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("bar timestamps must be strictly increasing")

    matrix = frame.loc[:, _COLUMNS].to_numpy(dtype="float64", copy=True)
    if not pd.notna(matrix).all():
        raise ValueError("bar frame contains non-finite values")
    price_format = f".{int(instrument.price_precision)}f"
    size_format = f".{int(instrument.size_precision)}f"
    output: list[Any] = []
    for timestamp, row in zip(frame.index, matrix, strict=True):
        open_, high, low, close, volume = (float(value) for value in row)
        if high < max(open_, close, low) or low > min(open_, close, high):
            raise ValueError(f"inconsistent OHLC at {timestamp.isoformat()}")
        if volume < 0:
            raise ValueError(f"negative volume at {timestamp.isoformat()}")
        ts_ns = int(timestamp.value)
        output.append(
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
    return output
