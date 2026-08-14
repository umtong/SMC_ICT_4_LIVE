#!/usr/bin/env python3
"""Runtime entry point for the sparse aggTrade absorption event study."""
from __future__ import annotations

from typing import Any
import zipfile

import pandas as pd

import aggtrade_absorption_event_study as _study


def _load_archive(symbol: str, day: Any, cache: Any) -> pd.DataFrame:
    """Read one verified archive with stable two-key chronological ordering."""
    archive = _study.archive_path(symbol, day, cache)
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected files in {archive}: {names}")
        with bundle.open(names[0]) as stream:
            frame = pd.read_csv(stream, header=None)
    if frame.shape[1] < len(_study.ARCHIVE_COLUMNS):
        raise RuntimeError(f"unexpected aggTrade schema in {archive}: {frame.shape}")
    frame = frame.iloc[:, : len(_study.ARCHIVE_COLUMNS)].copy()
    frame.columns = _study.ARCHIVE_COLUMNS
    if not str(frame.iloc[0]["aggregate_trade_id"]).lstrip("-").isdigit():
        frame = frame.iloc[1:].copy()
    numeric = (
        "aggregate_trade_id",
        "price",
        "quantity",
        "first_trade_id",
        "last_trade_id",
        "timestamp",
    )
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    for column in ("aggregate_trade_id", "first_trade_id", "last_trade_id", "timestamp"):
        frame[column] = frame[column].astype("int64")
    frame["price"] = frame["price"].astype("float64")
    frame["quantity"] = frame["quantity"].astype("float64")
    frame["buyer_is_maker"] = frame["buyer_is_maker"].map(_study._boolean)
    frame["quote"] = frame["price"] * frame["quantity"]
    frame["signed_quote"] = frame["quote"].where(
        ~frame["buyer_is_maker"],
        -frame["quote"],
    )
    return frame.sort_values(
        ["timestamp", "aggregate_trade_id"],
        kind="mergesort",
    ).reset_index(drop=True)


_study.load_archive = _load_archive


if __name__ == "__main__":
    _study.main()
