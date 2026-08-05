#!/usr/bin/env python3
"""Corrected causal rich features with time-based book-depth changes.

Binance's public ``bookDepth`` archive is sampled at approximately 30 seconds,
not at a fixed five-second cadence.  This wrapper replaces the row-count lag in
``rich_features.py`` with one-second causal forward filling and exact elapsed-
time lags.  A snapshot is never used before its exchange timestamp.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).with_name("rich_features.py")
SPEC = importlib.util.spec_from_file_location("candidate04_rich_features_base", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)


def aggregate_depth(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    raw["ts"] = pd.to_datetime(raw["timestamp"], utc=True)
    depth = raw.pivot(index="ts", columns="percentage", values="notional").sort_index()
    if depth.empty:
        raise RuntimeError(f"empty bookDepth archive: {path}")

    # Public files contain about 2,880 snapshots/day.  Reindexing at one second
    # makes elapsed-time changes exact despite the observed 25-35 second jitter.
    one_second = depth.resample("1s").ffill()
    minute_index = one_second.resample("1min").last().index
    minute = pd.DataFrame(index=minute_index)

    snapshot_time = pd.Series(depth.index, index=depth.index)
    observed_snapshot_time = snapshot_time.resample("1s").ffill().reindex(one_second.index)
    age = (one_second.index.to_series(index=one_second.index) - observed_snapshot_time).dt.total_seconds()
    minute["depth_snapshot_age_seconds"] = age.resample("1min").last()

    for band in (1, 2, 3, 4, 5):
        bid = one_second[-band]
        ask = one_second[band]
        total = bid + ask
        minute[f"depth_imb_{band}"] = ((bid - ask) / total.replace(0.0, np.nan)).resample("1min").last()
        minute[f"bid_depth_{band}"] = bid.resample("1min").last()
        minute[f"ask_depth_{band}"] = ask.resample("1min").last()
        for seconds in (30, 60, 300):
            minute[f"bid_chg_{band}_{seconds}s"] = (
                bid / bid.shift(seconds) - 1.0
            ).resample("1min").last()
            minute[f"ask_chg_{band}_{seconds}s"] = (
                ask / ask.shift(seconds) - 1.0
            ).resample("1min").last()

    return minute


BASE.aggregate_depth = aggregate_depth


if __name__ == "__main__":
    BASE.main()
