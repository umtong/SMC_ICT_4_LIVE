#!/usr/bin/env python3
"""Checksum-verified Binance Vision derivatives data contract.

Only observation construction lives here.  The module does not simulate
orders, positions, or account PnL.  It adapts the official Binance Vision path
convention for monthly USD-M premium-index klines and funding-rate archives and
retains taker-buy quote volume from ordinary spot/perpetual klines.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from hashlib import sha256
from pathlib import Path
import urllib.request
from typing import Any

import pandas as pd


VISION_MONTHLY = "https://data.binance.vision/data/futures/um/monthly"
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]


@dataclass(frozen=True, slots=True)
class MonthlyEvidence:
    endpoint: str
    symbol: str
    month: str
    interval: str | None
    archive: str
    checksum: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _monthly_spec(
    endpoint: str,
    symbol: str,
    month: str,
    interval: str | None,
) -> tuple[str, str]:
    if endpoint == "fundingRate":
        filename = f"{symbol}-fundingRate-{month}.zip"
        relative = f"fundingRate/{symbol}/{filename}"
    elif endpoint == "premiumIndexKlines":
        if not interval:
            raise ValueError("premiumIndexKlines requires an interval")
        filename = f"{symbol}-{interval}-{month}.zip"
        relative = f"premiumIndexKlines/{symbol}/{interval}/{filename}"
    else:
        raise ValueError(f"unsupported monthly endpoint: {endpoint}")
    return f"{VISION_MONTHLY}/{relative}", filename


def download_monthly_checked(
    endpoint: str,
    symbol: str,
    month: str,
    cache: Path,
    *,
    interval: str | None = None,
) -> tuple[Path, MonthlyEvidence]:
    url, filename = _monthly_spec(endpoint, symbol, month, interval)
    directory = cache / endpoint / symbol / (interval or "no_interval")
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    evidence = MonthlyEvidence(
        endpoint=endpoint,
        symbol=symbol,
        month=month,
        interval=interval,
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )
    return archive, evidence


def _drop_optional_header(raw: pd.DataFrame, column: int = 0) -> pd.DataFrame:
    if raw.empty:
        return raw
    first = str(raw.iloc[0, column]).strip()
    numeric = first.replace(".", "", 1).replace("-", "", 1).isdigit()
    return raw if numeric else raw.iloc[1:].copy()


def _timestamp_unit(series: pd.Series) -> str:
    first = float(pd.to_numeric(series, errors="raise").iloc[0])
    return "us" if first > 10**14 else "ms"


def read_full_kline(path: Path) -> pd.DataFrame:
    """Read ordinary spot/perpetual klines retaining taker-buy quote volume."""
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] != len(KLINE_COLUMNS):
        with_header = pd.read_csv(path, compression="zip")
        if not set(KLINE_COLUMNS).issubset(with_header.columns):
            raise RuntimeError(f"unexpected kline schema in {path}: {list(with_header.columns)}")
        raw = with_header[KLINE_COLUMNS].copy()
    else:
        raw.columns = KLINE_COLUMNS
        first = str(raw.iloc[0]["open_time"])
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()

    numeric = (
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "close_time",
        "quote_volume",
        "taker_buy_quote_volume",
    )
    for column in numeric:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["open_time_dt"] = pd.to_datetime(
        raw["open_time"].to_numpy(),
        unit=_timestamp_unit(raw["open_time"]),
        utc=True,
    )
    raw["close_time_dt"] = pd.to_datetime(
        raw["close_time"].to_numpy(),
        unit=_timestamp_unit(raw["close_time"]),
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
            "quote_volume",
            "taker_buy_quote_volume",
        ]
    ].copy()
    frame = frame.sort_values("open_time_dt")
    if frame["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate kline open times in {path}")
    return frame


def read_premium_index_kline(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    raw = _drop_optional_header(raw)
    if raw.shape[1] < 7:
        raise RuntimeError(f"unexpected premium-index schema in {path}: {raw.shape[1]} columns")
    frame = raw.iloc[:, [0, 1, 2, 3, 4, 6]].copy()
    frame.columns = ["open_time", "open", "high", "low", "close", "close_time"]
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["open_time_dt"] = pd.to_datetime(
        frame["open_time"].to_numpy(),
        unit=_timestamp_unit(frame["open_time"]),
        utc=True,
    )
    frame["close_time_dt"] = pd.to_datetime(
        frame["close_time"].to_numpy(),
        unit=_timestamp_unit(frame["close_time"]),
        utc=True,
    )
    frame = frame[
        ["open_time_dt", "close_time_dt", "open", "high", "low", "close"]
    ].sort_values("open_time_dt")
    if frame["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate premium-index open times in {path}")
    return frame


def read_funding_rate(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    raw = _drop_optional_header(raw)
    if raw.shape[1] < 3:
        raise RuntimeError(f"unexpected funding-rate schema in {path}: {raw.shape[1]} columns")
    frame = raw.iloc[:, [0, 1, 2]].copy()
    frame.columns = ["calc_time", "funding_interval_hours", "last_funding_rate"]
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["calc_time_dt"] = pd.to_datetime(
        frame["calc_time"].to_numpy(),
        unit=_timestamp_unit(frame["calc_time"]),
        utc=True,
    )
    frame = frame[
        ["calc_time_dt", "funding_interval_hours", "last_funding_rate"]
    ].sort_values("calc_time_dt")
    if frame["calc_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate funding timestamps in {path}")
    return frame


def months_between(start: date, end: date) -> list[str]:
    cursor_year, cursor_month = start.year, start.month
    result: list[str] = []
    while (cursor_year, cursor_month) <= (end.year, end.month):
        result.append(f"{cursor_year:04d}-{cursor_month:02d}")
        cursor_month += 1
        if cursor_month == 13:
            cursor_month = 1
            cursor_year += 1
    return result
