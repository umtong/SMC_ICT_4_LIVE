#!/usr/bin/env python3
"""Compatibility wrapper for the fixed cross-asset transfer screen.

The original fixed screen is retained unchanged.  Pandas 3 parses numeric
strings passed to ``to_datetime(..., unit=...)`` as date strings, so older
Binance archives can overflow even though their millisecond timestamps are
valid.  This wrapper replaces only the kline reader with an explicitly numeric
implementation and then delegates all event logic to the frozen screen.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(CANDIDATE05))
sys.path.insert(0, str(HERE))

import cross_asset_transfer_screen as screen
from features import KLINE_COLUMNS


def robust_read_kline(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] == len(KLINE_COLUMNS):
        raw.columns = KLINE_COLUMNS
        first = str(raw.iloc[0]["open_time"])
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    else:
        with_header = pd.read_csv(path, compression="zip")
        if not set(KLINE_COLUMNS).issubset(with_header.columns):
            raise RuntimeError(
                f"unexpected kline schema in {path}: {list(with_header.columns)}",
            )
        raw = with_header[KLINE_COLUMNS].copy()

    numeric_columns = (
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
    )
    for column in numeric_columns:
        raw[column] = pd.to_numeric(raw[column], errors="raise")

    open_unit = "us" if float(raw["open_time"].iloc[0]) > 10**14 else "ms"
    close_unit = "us" if float(raw["close_time"].iloc[0]) > 10**14 else "ms"
    raw["open_time_dt"] = pd.to_datetime(
        raw["open_time"].to_numpy(), unit=open_unit, utc=True,
    )
    raw["close_time_dt"] = pd.to_datetime(
        raw["close_time"].to_numpy(), unit=close_unit, utc=True,
    )
    frame = raw[
        [
            "open_time_dt",
            "close_time_dt",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
        ]
    ].copy()
    frame = frame.sort_values("close_time_dt")
    if frame["close_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate kline close times in {path}")
    return frame


screen.read_kline = robust_read_kline


if __name__ == "__main__":
    screen.main()
