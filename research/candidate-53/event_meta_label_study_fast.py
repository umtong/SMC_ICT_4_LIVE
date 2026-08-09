#!/usr/bin/env python3
"""Parallel-I/O launcher for the frozen Candidate 53 meta-label study.

No signal, model, threshold, split, target, or cost changes. Only independent
checksum-verified monthly archive downloads are parallelized.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

import event_meta_label_study as base


def parallel_load_symbol(symbol: str, cache: Path) -> pd.DataFrame:
    labels = base._month_labels("2024-12", "2026-07")
    requests = []
    for label in labels:
        requests.append(("perp", base.Archive("um", "monthly", "klines", symbol, label, "1m")))
        requests.append(("spot", base.Archive("spot", "monthly", "klines", symbol, label, "1m")))

    def fetch(item):
        kind, archive = item
        path = base.download_verified(archive, cache / symbol / kind)
        return kind, archive.label, base.read_kline(path, prefix=kind)

    with ThreadPoolExecutor(max_workers=8) as pool:
        loaded = list(pool.map(fetch, requests))
    keyed = {(kind, label): frame for kind, label, frame in loaded}
    frames = []
    for label in labels:
        perp = keyed[("perp", label)]
        spot = keyed[("spot", label)]
        perp["minute"] = pd.to_datetime(perp["minute"], utc=True, errors="raise")
        spot["minute"] = pd.to_datetime(spot["minute"], utc=True, errors="raise")
        frames.append(
            perp.merge(
                spot[["minute", "spot_open", "spot_high", "spot_low", "spot_close", "spot_quote_volume"]],
                on="minute",
                how="inner",
                validate="one_to_one",
            )
        )
    panel = pd.concat(frames, ignore_index=True).sort_values("minute", kind="stable").drop_duplicates("minute", keep="last")
    if panel["minute"].duplicated().any() or not panel["minute"].is_monotonic_increasing:
        raise base.StudyError(f"invalid minute clock: {symbol}")
    return panel.set_index("minute", drop=False)


base.load_symbol = parallel_load_symbol
base.main()
