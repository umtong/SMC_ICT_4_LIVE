"""Checksum-verified Binance Vision klines with exact aggressor-flow fields.

The existing loader intentionally kept only OHLCV. Binance's daily USD-M kline
files already contain quote volume, trade count, taker-buy base volume and
taker-buy quote volume. This module preserves those fields without changing the
proven checksum/download path.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from data import BASE, COLUMNS, _download, _timestamp_unit, sha256_file


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


def load_day_flow(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    """Load one Binance USD-M one-minute kline day with exact flow fields."""
    stamp = day.isoformat()
    name = f"{symbol}-1m-{stamp}.zip"
    url = f"{BASE}/{symbol}/1m/{name}"
    archive = cache / symbol / name
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
            raise RuntimeError(f"unexpected kline schema: {list(raw.columns)}")
        raw = raw[COLUMNS]
    else:
        raw.columns = COLUMNS
        if not str(raw.iloc[0]["open_time"]).lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()

    numeric = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    )
    for column in numeric:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["count"] = raw["count"].astype("int64")

    open_values = pd.to_numeric(raw["open_time"], errors="raise")
    raw["open_time_dt"] = pd.to_datetime(
        open_values,
        unit=_timestamp_unit(open_values),
        utc=True,
    )
    return raw[FLOW_COLUMNS].sort_values("open_time_dt")


def load_range_flow(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    """Load a complete date range while retaining exact taker-side summaries."""
    frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        frames.append(load_day_flow(symbol, day, cache))
        day += timedelta(days=1)
    frame = pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    if frame["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate one-minute bars for {symbol}")
    expected_days = (end - start).days + 1
    if len(frame) < expected_days * 1430:
        raise RuntimeError(f"incomplete one-minute data for {symbol}: {len(frame)}")
    return frame


def wrangler_flow_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return close-timestamp-indexed one-minute input for NautilusTrader."""
    data = frame[FLOW_COLUMNS].copy()
    data.index = pd.DatetimeIndex(data.pop("open_time_dt")) + pd.Timedelta(minutes=1)
    return data
