"""Checksum-verified Binance Vision funding replay through native Nautilus data.

Funding rates and mark prices are both sourced from Binance's public archive:

    data/futures/um/monthly/fundingRate/{symbol}/
    data/futures/um/monthly/markPriceKlines/{symbol}/1m/

The funding archive states the realized rate and its actual interval per row.
The mark-price one-minute bar which opens exactly at the funding boundary
provides the price already observable at that instant; no future bar close is
used.  At a shared timestamp, the source trading bar is replayed first, then the
mark price, then the funding event.  NautilusTrader owns settlement, position
adjustment and account accounting.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import FundingRateUpdate, MarkPriceUpdate

from data import COLUMNS as KLINE_COLUMNS
from data import _download, sha256_file

FUNDING_BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
MARK_BASE = "https://data.binance.vision/data/futures/um/monthly/markPriceKlines"
NS_PER_MS = 1_000_000
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
    # Futures archives historically use milliseconds. Keep the same defensive
    # conversion as the existing kline loader in case an archive migrates to us.
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
            raise RuntimeError(
                f"invalid funding row for {symbol} {stamp}: {item!r}",
            ) from exc
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
            raise RuntimeError(
                f"invalid mark-price row for {symbol} {stamp}: {item!r}",
            ) from exc
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
    """Return realized funding rows with causal boundary mark prices.

    The evaluation's final one-minute bar closes at 00:00 UTC on the following
    day. A position open at that boundary is settled before evaluation flatten,
    so the endpoint is inclusive of ``end + 1 day``.
    """
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
        mark = mark_by_time.get(time_ms)
        if mark is None:
            raise RuntimeError(f"missing causal mark-price open for {symbol} at {time_ms}")
        enriched = dict(row)
        enriched["markPrice"] = mark
        selected.append(enriched)

    if not selected:
        raise RuntimeError(f"no in-range funding settlements for {symbol}")
    return selected


def add_symbol_funding_data(
    engine: BacktestEngine,
    symbol: str,
    instrument: object,
    start: date,
    end: date,
    cache: Path,
) -> dict[str, object]:
    """Add mark-price and realized funding events to a Nautilus backtest."""
    rows = load_funding_history(symbol, start, end, cache)
    marks: list[MarkPriceUpdate] = []
    funding: list[FundingRateUpdate] = []
    intervals: Counter[int] = Counter()
    for row in rows:
        time_ns = int(row["fundingTime"]) * NS_PER_MS
        interval = int(row["intervalMinutes"])
        intervals[interval] += 1
        marks.append(
            MarkPriceUpdate(
                instrument_id=instrument.id,
                value=instrument.make_price(row["markPrice"]),
                ts_event=time_ns,
                # One-minute source bars at this close run first. The mark open
                # is observable at the boundary before funding is settled.
                ts_init=time_ns + 1,
            ),
        )
        funding.append(
            FundingRateUpdate(
                instrument_id=instrument.id,
                rate=row["fundingRate"],
                ts_event=time_ns,
                ts_init=time_ns + 2,
                interval=interval,
                # Historical rows are realized boundaries, not forecasts.
                next_funding_ns=time_ns,
            ),
        )
    engine.add_data(marks, sort=False)
    engine.add_data(funding, sort=False)
    return {
        "records": len(rows),
        "first_funding_time_ms": int(rows[0]["fundingTime"]),
        "last_funding_time_ms": int(rows[-1]["fundingTime"]),
        "interval_minutes": dict(sorted(intervals.items())),
        "rate_sum": str(sum((row["fundingRate"] for row in rows), Decimal("0"))),
        "source": "Binance Vision fundingRate + 1m markPriceKlines",
        "checksum_verified": True,
    }
