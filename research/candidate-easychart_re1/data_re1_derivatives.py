"""Checksum-verified Binance Vision positioning and funding data.

The EasyChart candidates currently infer inventory transfer from OHLCV and
kline taker summaries. Binance Vision also publishes native five-minute USD-M
``metrics`` snapshots (open interest, account/position ratios, taker ratio) and
monthly funding-rate archives. These fields expose a different state dimension:
whether a price move is accompanied by position creation, position destruction,
or crowding.

This module is research infrastructure only. Timestamps are treated as
point-in-time observations and later joined strictly backward (``<= plan time``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from data import _download, sha256_file


METRICS_BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
FUNDING_BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"

METRICS_COLUMNS = [
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]
FUNDING_COLUMNS = [
    "calc_time",
    "funding_interval_hours",
    "last_funding_rate",
]


@dataclass(frozen=True)
class DerivativesRange:
    metrics: pd.DataFrame
    funding: pd.DataFrame


def _verified_archive(url: str, archive: Path) -> Path:
    checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
    _download(url, archive)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch {archive}: {actual} != {expected}")
    return archive


def _read_archive(archive: Path, columns: list[str]) -> pd.DataFrame:
    raw = pd.read_csv(archive, compression="zip")
    if set(columns).issubset(raw.columns):
        return raw[columns].copy()

    raw = pd.read_csv(archive, compression="zip", header=None)
    if raw.shape[1] != len(columns):
        raise RuntimeError(
            f"unexpected schema in {archive}: {raw.shape[1]} columns, "
            f"expected {len(columns)}",
        )
    raw.columns = columns
    if not raw.empty and str(raw.iloc[0, 0]).strip().lower() == columns[0]:
        raw = raw.iloc[1:].copy()
    return raw


def load_day_metrics(symbol: str, day: date, cache: Path) -> pd.DataFrame:
    stamp = day.isoformat()
    name = f"{symbol}-metrics-{stamp}.zip"
    url = f"{METRICS_BASE}/{symbol}/{name}"
    archive = cache / "metrics" / symbol / name
    _verified_archive(url, archive)
    raw = _read_archive(archive, METRICS_COLUMNS)

    raw["create_time"] = pd.to_datetime(raw["create_time"], utc=True, errors="raise")
    for column in METRICS_COLUMNS[2:]:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    if not raw.empty:
        observed = set(raw["symbol"].astype(str).unique())
        if observed != {symbol}:
            raise RuntimeError(f"unexpected metric symbols for {symbol}: {observed}")
    raw = raw.sort_values("create_time").drop_duplicates("create_time", keep="last")
    if len(raw) < 270:
        raise RuntimeError(f"incomplete five-minute metrics for {symbol} {stamp}: {len(raw)}")
    return raw.reset_index(drop=True)


def load_range_metrics(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        frames.append(load_day_metrics(symbol, day, cache))
        day += timedelta(days=1)
    output = pd.concat(frames, ignore_index=True).sort_values("create_time")
    output = output.drop_duplicates("create_time", keep="last")
    if output["create_time"].duplicated().any():
        raise RuntimeError(f"duplicate derivatives metrics for {symbol}")
    return output.reset_index(drop=True)


def _month_floor(day: date) -> date:
    return date(day.year, day.month, 1)


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)


def load_month_funding(symbol: str, month: date, cache: Path) -> pd.DataFrame:
    month = _month_floor(month)
    stamp = month.strftime("%Y-%m")
    name = f"{symbol}-fundingRate-{stamp}.zip"
    url = f"{FUNDING_BASE}/{symbol}/{name}"
    archive = cache / "fundingRate" / symbol / name
    _verified_archive(url, archive)
    raw = _read_archive(archive, FUNDING_COLUMNS)

    calc_values = pd.to_numeric(raw["calc_time"], errors="raise")
    unit = "us" if not calc_values.empty and abs(int(calc_values.iloc[0])) >= 10**15 else "ms"
    raw["calc_time"] = pd.to_datetime(calc_values, unit=unit, utc=True)
    raw["funding_interval_hours"] = pd.to_numeric(
        raw["funding_interval_hours"],
        errors="raise",
    )
    raw["last_funding_rate"] = pd.to_numeric(
        raw["last_funding_rate"],
        errors="raise",
    )
    return (
        raw.sort_values("calc_time")
        .drop_duplicates("calc_time", keep="last")
        .reset_index(drop=True)
    )


def load_range_funding(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    month = _month_floor(start)
    final_month = _month_floor(end)
    while month <= final_month:
        frames.append(load_month_funding(symbol, month, cache))
        month = _next_month(month)
    output = pd.concat(frames, ignore_index=True).sort_values("calc_time")
    mask = (
        output["calc_time"] >= pd.Timestamp(start, tz="UTC")
    ) & (
        output["calc_time"] < pd.Timestamp(end + timedelta(days=1), tz="UTC")
    )
    return output.loc[mask].drop_duplicates("calc_time", keep="last").reset_index(drop=True)


def load_range_derivatives(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> DerivativesRange:
    return DerivativesRange(
        metrics=load_range_metrics(symbol, start, end, cache),
        funding=load_range_funding(symbol, start, end, cache),
    )
