#!/usr/bin/env python3
"""Run the frozen V56 router for one allowed experiment symbol."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

import prominence_state_router as base


ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}


def load_symbol_rich(directory: Path) -> pd.DataFrame:
    symbol = os.environ.get("C04_SYMBOL", directory.name)
    if symbol not in ALLOWED_SYMBOLS:
        raise RuntimeError(f"unsupported V56 symbol: {symbol}")
    files = sorted(directory.glob(f"{symbol}-rich-*.csv.gz"))
    if not files:
        raise RuntimeError(f"no {symbol} rich features in {directory}")
    frame = pd.concat((pd.read_csv(path) for path in files), ignore_index=True)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame["observed_time"] = pd.to_datetime(frame["observed_time"], utc=True)
    frame = frame.sort_values("open_time").drop_duplicates("open_time")
    frame = frame.set_index("open_time")
    expected = frame.index + pd.Timedelta(minutes=1)
    if not (frame["observed_time"].array == expected.array).all():
        raise RuntimeError("rich features violate close-observed contract")
    return frame


base.load_rich = load_symbol_rich


if __name__ == "__main__":
    base.main()
