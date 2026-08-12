"""Checksum-verified Binance Vision funding history for Nautilus settlement.

Funding rates and mark prices are both sourced from Binance's public archive:

    data/futures/um/monthly/fundingRate/{symbol}/
    data/futures/um/monthly/markPriceKlines/{symbol}/1m/

The funding archive states the realized rate and its actual interval per row.
Some historical ``calc_time`` values are a few milliseconds after the nominal
clock boundary. The containing one-minute mark-price bar open is therefore the
last causal mark observation at settlement; no future bar close is used.
The resulting immutable boundaries are consumed by the project's Nautilus
``SimulationModule`` so the exchange remains responsible for account events
and continuous NAV.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from data import COLUMNS as KLINE_COLUMNS
from data import _download, sha256_file
from funding_module import HistoricalFundingBoundary

FUNDING_BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
MARK_BASE = "https://data.binance.vision/data/futures/um/monthly/markPriceKlines"
NS_PER_MS = 1_000_000
MS_PER_MINUTE = 60_000
FUNDING_COLUMNS = ("calc_time", "funding_interval_hours", "last_funding_rate")


def _utc_ms(day: date) -> int:
    return int(pd.Timestamp(day, tz="UTC").value // NS_PER_MS)


def _month_floor(day: date) -> date:
    return date(day.year, day.month, 1)


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)


def _months(start: date, end: date) -> list[str]:
    if end < start:
        raise ValueError("month range end must be >= start")
    cursor = _month_floor(start)
    last = _month_floor(end)
    stamps: list[str] = []
    while cursor <= last:
        stamps.append(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor = _next_month(cursor)
    return stamps


def _verified_archive(url: str, path: Path) -> Path:
    checksum = path.with_suffix(path.suffix + ".CHECKSUM")
    _download(url, path)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(path)
    if actual != expected:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch {path}: {actual} != {expected}")
    return path


def _timestamp_ms(value: object) -> int:
    integer = int(str(value).strip())
    return integer // 1000 if abs(integer) >= 10**15 else integer


def _read_funding_month(symbol: str, stamp: str, cache: Path) -> list[dict[str, object]]:
    name = f"{symbol}-fundingRate-{stamp}.zip"
    archive = _verified_archive(
        f"{FUNDING_BASE}/{symbol}/{name}",
        cache / "funding" / symbol / name,
    )
    raw = pd.read_csv(archive, compression="zip", header=None, dtype=str)
    if raw.shape[1] != len(FUNDING_COLUMNS):
        raise RuntimeError(f"unexpected funding schema width for {symbol} {stamp}: {raw.shape}")
    raw.columns = FUNDING_COLUMNS
    if str(raw.iloc[0]["calc_time"]).strip() == "calc_time":
        raw = raw.iloc[1:].copy()
    rows: list[dict[str, object]] = []
    for item in raw.itertuples(index=False):
        try:
            time_ms = _timestamp_ms(item.calc_time)
            interval_hours = int(str(item.funding_interval_hours).strip())
            rate = Decimal(str(item.last_funding_rate).strip())
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid funding row for {symbol} {stamp}: {item!r}") from exc
        if not rate.is_finite() or not 1 <= interval_hours <= 1092:
            raise RuntimeError(f"invalid funding values for {symbol} {stamp}: {item!r}")
        rows.append(
            {
                "symbol": symbol,
                "fundingTime": time_ms,
                "fundingRate": rate,
                "intervalMinutes": interval_hours * 60,
            },
        )
    return rows


def _read_mark_month(symbol: str, stamp: str, cache: Path) -> dict[int, Decimal]:
    name = f"{symbol}-1m-{stamp}.zip"
    archive = _verified_archive(
        f"{MARK_BASE}/{symbol}/1m/{name}",
        cache / "mark-price" / symbol / name,
    )
    raw = pd.read_csv(archive, compression="zip", header=None, dtype=str)
    if raw.shape[1] != len(KLINE_COLUMNS):
        raise RuntimeError(f"unexpected mark-price schema width for {symbol} {stamp}: {raw.shape}")
    raw.columns = KLINE_COLUMNS
    if str(raw.iloc[0]["open_time"]).strip() == "open_time":
        raw = raw.iloc[1:].copy()
    values: dict[int, Decimal] = {}
    for item in raw.itertuples(index=False):
        try:
            time_ms = _timestamp_ms(item.open_time)
            price = Decimal(str(item.open).strip())
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid mark-price row for {symbol} {stamp}: {item!r}") from exc
        if not price.is_finite() or price <= 0:
            raise RuntimeError(f"invalid mark price for {symbol} {stamp}: {item!r}")
        previous = values.setdefault(time_ms, price)
        if previous != price:
            raise RuntimeError(f"conflicting mark prices for {symbol} at {time_ms}")
    return values


def load_funding_history(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> list[dict[str, object]]:
    """Return realized funding rows with causal containing-minute mark opens."""
    if end < start:
        raise ValueError("funding end must be >= start")
    first_ms = _utc_ms(start)
    last_ms = _utc_ms(end + timedelta(days=1))
    stamps = _months(start, end + timedelta(days=1))

    raw_rows: list[dict[str, object]] = []
    mark_by_time: dict[int, Decimal] = {}
    for stamp in stamps:
        raw_rows.extend(_read_funding_month(symbol, stamp, cache))
        for time_ms, price in _read_mark_month(symbol, stamp, cache).items():
            old = mark_by_time.setdefault(time_ms, price)
            if old != price:
                raise RuntimeError(f"conflicting cross-archive mark price for {symbol} {time_ms}")

    selected: list[dict[str, object]] = []
    seen: set[int] = set()
    for row in sorted(raw_rows, key=lambda item: int(item["fundingTime"])):
        time_ms = int(row["fundingTime"])
        if not first_ms <= time_ms <= last_ms:
            continue
        if time_ms in seen:
            raise RuntimeError(f"duplicate funding boundary for {symbol}: {time_ms}")
        seen.add(time_ms)
        mark_open_ms = time_ms - time_ms % MS_PER_MINUTE
        mark = mark_by_time.get(mark_open_ms)
        if mark is None:
            raise RuntimeError(
                f"missing causal containing-minute mark open for {symbol} "
                f"at funding {time_ms} / mark {mark_open_ms}",
            )
        enriched = dict(row)
        enriched["markOpenTime"] = mark_open_ms
        enriched["fundingTimeOffsetMs"] = time_ms - mark_open_ms
        enriched["markPrice"] = mark
        selected.append(enriched)

    if not selected:
        raise RuntimeError(f"no in-range funding settlements for {symbol}")
    return selected


def build_symbol_funding_boundaries(
    symbol: str,
    instrument: Any,
    start: date,
    end: date,
    cache: Path,
) -> tuple[list[HistoricalFundingBoundary], dict[str, object]]:
    """Build immutable realized boundaries and an auditable source summary."""
    rows = load_funding_history(symbol, start, end, cache)
    intervals: Counter[int] = Counter()
    offsets: Counter[int] = Counter()
    boundaries: list[HistoricalFundingBoundary] = []
    for row in rows:
        interval = int(row["intervalMinutes"])
        intervals[interval] += 1
        offsets[int(row["fundingTimeOffsetMs"])] += 1
        boundaries.append(
            HistoricalFundingBoundary(
                symbol=symbol,
                instrument_id=instrument.id,
                funding_time_ns=int(row["fundingTime"]) * NS_PER_MS,
                interval_minutes=interval,
                rate=Decimal(row["fundingRate"]),
                mark_price=Decimal(row["markPrice"]),
            ),
        )
    summary = {
        "records": len(rows),
        "first_funding_time_ms": int(rows[0]["fundingTime"]),
        "last_funding_time_ms": int(rows[-1]["fundingTime"]),
        "interval_minutes": dict(sorted(intervals.items())),
        "funding_time_offset_ms": dict(sorted(offsets.items())),
        "rate_sum": str(sum((Decimal(row["fundingRate"]) for row in rows), Decimal("0"))),
        "source": "Binance Vision fundingRate + 1m markPriceKlines",
        "mark_policy": "open of the one-minute bar containing calc_time",
        "checksum_verified": True,
    }
    return boundaries, summary
