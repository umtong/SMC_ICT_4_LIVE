#!/usr/bin/env python3
"""Compatibility runner for the fixed cross-asset event screen.

Older Binance archives expose millisecond timestamps as object/string columns.
Recent pandas versions can ignore ``unit='ms'`` for such objects and parse the
integer text as a calendar year.  The shared reader remains untouched; this
small adapter coerces only the two timestamp columns to int64 before datetime
conversion, then installs it into the fixed screen module.

The ten-minute horizon is included before observing screen results because the
external minute-frequency research explicitly reports predictability up to ten
minutes and uses ten-minute portfolio rebalancing.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(CANDIDATE05))

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

    for column in (
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
    ):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    open_unit = "us" if int(raw["open_time"].iloc[0]) > 10**14 else "ms"
    close_unit = "us" if int(raw["close_time"].iloc[0]) > 10**14 else "ms"
    raw["open_time_dt"] = pd.to_datetime(
        raw["open_time"].astype("int64"),
        unit=open_unit,
        utc=True,
    )
    raw["close_time_dt"] = pd.to_datetime(
        raw["close_time"].astype("int64"),
        unit=close_unit,
        utc=True,
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
screen.HORIZONS = (1, 3, 5, 10, 15, 30, 60)


if __name__ == "__main__":
    screen.main()
