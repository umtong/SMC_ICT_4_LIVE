#!/usr/bin/env python3
"""Runtime-corrected exact aggregate-trade tape.

The archive is read header-neutral so both historical headerless files and
newer headered files retain every trade. Missing cross-asset fields are filled
explicitly rather than relying on tuple-tail positions.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_tape_v10 as v10
from mechanism_data_v2 import _verified_archive

base = v10.base
FEATURE_COLUMNS = v10.FEATURE_COLUMNS
TAPE_FEATURE_COLUMNS = v10.TAPE_FEATURE_COLUMNS
SYMBOLS = v10.SYMBOLS


def load_aggtrades_day(symbol: str, day: date, cache: Path) -> v10.TapeStore:
    stamp = day.isoformat()
    name = f"{symbol}-aggTrades-{stamp}.zip"
    url = f"{v10.VISION}/{symbol}/{name}"
    path = _verified_archive(url, cache / symbol / name)
    frame = pd.read_csv(path, compression="zip", header=None, low_memory=False)
    if frame.shape[1] < 7:
        raise RuntimeError(f"unexpected aggTrades schema in {path}: {frame.shape}")
    first_price = str(frame.iloc[0, 1]).strip().lower()
    if first_price in ("price", "p"):
        frame = frame.iloc[1:].copy()
    price = pd.to_numeric(frame.iloc[:, 1], errors="coerce").to_numpy(float)
    quantity = pd.to_numeric(frame.iloc[:, 2], errors="coerce").to_numpy(float)
    time_ns = v10._timestamp_ns(frame.iloc[:, 5])
    sign = v10._buyer_maker_sign(frame.iloc[:, 6])
    valid = (
        np.isfinite(price)
        & np.isfinite(quantity)
        & (price > 0.0)
        & (quantity > 0.0)
        & (time_ns > 0)
    )
    if not np.any(valid):
        return v10.TapeStore.empty()
    time_ns = time_ns[valid]
    price = price[valid]
    quote = price * quantity[valid]
    sign = sign[valid]
    order = np.argsort(time_ns, kind="stable")
    return v10.TapeStore(
        time_ns=time_ns[order].astype(np.int64, copy=False),
        price=price[order].astype(np.float64, copy=False),
        quote=quote[order].astype(np.float64, copy=False),
        sign=sign[order].astype(np.int8, copy=False),
    )


_previous_decision_features: Any = None


def _decision_features(*args: Any, **kwargs: Any) -> dict[str, Any]:
    values = _previous_decision_features(*args, **kwargs)
    for column in TAPE_FEATURE_COLUMNS:
        values.setdefault(column, np.nan)
    return values


def _install() -> None:
    global _previous_decision_features
    _previous_decision_features = v10._decision_features
    v10.load_aggtrades_day = load_aggtrades_day
    v10._decision_features = _decision_features


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v11"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    _install()
    args = parse_args()
    v10.harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
