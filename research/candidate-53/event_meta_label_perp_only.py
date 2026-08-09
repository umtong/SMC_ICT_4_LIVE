#!/usr/bin/env python3
"""Perpetual-only input ablation of Candidate 53 frozen meta-label study.

Only the data layer changes: spot OHLC fields are set equal to completed perp
OHLC, making basis zero and spot alignment redundant. Primary events, features,
model form, splits, costs, target geometry and probability threshold are
otherwise unchanged. This is both faster and a direct test of spot's incremental
value.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
import event_meta_label_study as base


def load_perp_only(symbol: str, cache: Path) -> pd.DataFrame:
    labels = base._month_labels("2024-12", "2026-07")
    def fetch(label: str):
        archive = base.Archive("um", "monthly", "klines", symbol, label, "1m")
        path = base.download_verified(archive, cache / symbol / "perp")
        return label, base.read_kline(path, prefix="perp")
    with ThreadPoolExecutor(max_workers=10) as pool:
        keyed = dict(pool.map(fetch, labels))
    frames=[]
    for label in labels:
        frame=keyed[label]
        frame["minute"] = pd.to_datetime(frame["minute"], utc=True, errors="raise")
        frame["spot_open"] = frame["perp_open"]
        frame["spot_high"] = frame["perp_high"]
        frame["spot_low"] = frame["perp_low"]
        frame["spot_close"] = frame["perp_close"]
        frame["spot_quote_volume"] = frame["perp_quote_volume"]
        frames.append(frame)
    panel=pd.concat(frames,ignore_index=True).sort_values("minute",kind="stable").drop_duplicates("minute",keep="last")
    if panel["minute"].duplicated().any() or not panel["minute"].is_monotonic_increasing:
        raise base.StudyError(f"invalid minute clock: {symbol}")
    return panel.set_index("minute",drop=False)

base.load_symbol = load_perp_only
base.main()
