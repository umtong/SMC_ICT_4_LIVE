"""Checksum-verified Binance spot one-minute klines with aggressor fields.

Spot/perpetual disagreement is economically useful: a derivatives-only move with
open-interest contraction is a different auction from spot-led demand that is
accepted by both venues.  This loader mirrors the existing USD-M ingestion and
preserves quote volume, trade count and taker-buy fields.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from data import COLUMNS, _download, _timestamp_unit, sha256_file

BASE = "https://data.binance.vision/data/spot/daily/klines"
FLOW_COLUMNS = [
    "open_time_dt",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
]


def load_spot_day_flow(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    stamp = day.isoformat()
    name = f"{symbol}-1m-{stamp}.zip"
    url = f"{BASE}/{symbol}/1m/{name}"
    archive = cache / "spot" / symbol / name
    checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
    _download(url, archive)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch {archive}: {actual} != {expected}")

    raw = pd.read_csv(archive, compression="zip", header=None)
    if raw.shape[1] != len(COLUMNS):
        raw = pd.read_csv(archive, compression="zip")
        if not set(COLUMNS).issubset(raw.columns):
            raise RuntimeError(f"unexpected spot kline schema: {list(raw.columns)}")
        raw = raw[COLUMNS]
    else:
        raw.columns = COLUMNS
        if not str(raw.iloc[0]["open_time"]).lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()

    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    ):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["count"] = raw["count"].astype("int64")
    open_values = pd.to_numeric(raw["open_time"], errors="raise")
    raw["open_time_dt"] = pd.to_datetime(
        open_values,
        unit=_timestamp_unit(open_values),
        utc=True,
    )
    return raw[FLOW_COLUMNS].sort_values("open_time_dt")


def load_spot_range_flow(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        frames.append(load_spot_day_flow(symbol, day, cache))
        day += timedelta(days=1)
    frame = pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    if frame["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate spot one-minute bars for {symbol}")
    expected_days = (end - start).days + 1
    if len(frame) < expected_days * 1430:
        raise RuntimeError(f"incomplete spot one-minute data for {symbol}: {len(frame)}")
    return frame
