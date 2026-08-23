"""Checksum-verified Binance USD-M mark/index one-minute kline loaders."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd


COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
ALLOWED_KINDS = {"markPriceKlines", "indexPriceKlines", "premiumIndexKlines"}
BASE = "https://data.binance.vision/data/futures/um/daily"


def _time_unit(values: pd.Series) -> str:
    first = int(pd.to_numeric(values, errors="raise").iloc[0])
    if abs(first) >= 10**15:
        return "us"
    if abs(first) >= 10**12:
        return "ms"
    return "s"


def load_day(kind: str, symbol: str, day: date, cache: Path) -> pd.DataFrame:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported kline kind {kind}")
    from data import _download, sha256_file

    stamp = day.isoformat()
    name = f"{symbol}-1m-{stamp}.zip"
    url = f"{BASE}/{kind}/{symbol}/1m/{name}"
    archive = cache / kind / symbol / name
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
        if not set(COLUMNS[:6]).issubset(raw.columns):
            raise RuntimeError(f"unexpected {kind} schema: {list(raw.columns)}")
        raw = raw.reindex(columns=COLUMNS)
    else:
        raw.columns = COLUMNS
        if not str(raw.iloc[0]["open_time"]).lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    for column in ("open", "high", "low", "close"):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    values = pd.to_numeric(raw["open_time"], errors="raise")
    raw["open_time_dt"] = pd.to_datetime(values, unit=_time_unit(values), utc=True)
    output = raw[["open_time_dt", "open", "high", "low", "close"]].copy()
    return output.sort_values("open_time_dt").drop_duplicates("open_time_dt", keep="last")


def load_range(kind: str, symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        frames.append(load_day(kind, symbol, day, cache))
        day += timedelta(days=1)
    frame = pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    if frame["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate {kind} bars for {symbol}")
    expected_days = (end - start).days + 1
    if len(frame) < expected_days * 1430:
        raise RuntimeError(f"incomplete {kind} data for {symbol}: {len(frame)}")
    return frame
