"""Checksum-verified Binance USD-M funding and mark-price ingestion.

Funding is an account cash flow, not a setup filter. This module loads the
venue's realized funding observations and the last completed one-minute mark
price before each settlement boundary, then emits native NautilusTrader data
objects. No future rate or future mark price is exposed before its timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import FundingRateUpdate, MarkPriceUpdate

from data import _download, sha256_file


FUNDING_BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
MARK_BASE = "https://data.binance.vision/data/futures/um/monthly/markPriceKlines"
FUNDING_COLUMNS = ("calc_time", "funding_interval_hours", "last_funding_rate")
KLINE_COLUMNS = (
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
)
MAX_MARK_AGE_NS = 120 * 1_000_000_000
FUNDING_PROVENANCE = (
    "BINANCE_VISION_USD_M_MONTHLY_FUNDING_RATE_WITH_PRECEDING_1M_MARK_CLOSE"
)


@dataclass(frozen=True, slots=True)
class FundingObservation:
    timestamp_ns: int
    interval_minutes: int
    rate: Decimal


@dataclass(frozen=True, slots=True)
class MarkObservation:
    timestamp_ns: int
    value: Decimal


@dataclass(frozen=True, slots=True)
class FundingMarkPair:
    funding: FundingObservation
    mark: MarkObservation


def _timestamp_multiplier(first: int) -> int:
    magnitude = abs(first)
    if magnitude >= 10**17:
        return 1  # nanoseconds
    if magnitude >= 10**14:
        return 1_000  # microseconds
    if magnitude >= 10**11:
        return 1_000_000  # milliseconds
    return 1_000_000_000  # seconds


def epoch_values_to_ns(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    if numeric.empty:
        return numeric
    multiplier = _timestamp_multiplier(int(numeric.iloc[0]))
    maximum = int(numeric.abs().max())
    if maximum > (2**63 - 1) // multiplier:
        raise ValueError("timestamp conversion would overflow int64 nanoseconds")
    return numeric * multiplier


def parse_funding_frame(frame: pd.DataFrame) -> list[FundingObservation]:
    if not set(FUNDING_COLUMNS).issubset(frame.columns):
        raise ValueError(f"unexpected funding schema: {list(frame.columns)}")
    timestamps = epoch_values_to_ns(frame["calc_time"])
    intervals = pd.to_numeric(frame["funding_interval_hours"], errors="raise").astype("int64")
    if (intervals <= 0).any():
        raise ValueError("funding intervals must be positive")
    observations = [
        FundingObservation(
            timestamp_ns=int(timestamp_ns),
            interval_minutes=int(interval_hours) * 60,
            rate=Decimal(str(rate_text)),
        )
        for timestamp_ns, interval_hours, rate_text in zip(
            timestamps,
            intervals,
            frame["last_funding_rate"].astype(str),
            strict=True,
        )
    ]
    observations.sort(key=lambda item: item.timestamp_ns)
    if len({item.timestamp_ns for item in observations}) != len(observations):
        raise ValueError("duplicate funding settlement timestamp")
    return observations


def parse_mark_frame(frame: pd.DataFrame) -> list[MarkObservation]:
    required = {"close_time", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"unexpected mark-price kline schema: {list(frame.columns)}")
    timestamps = epoch_values_to_ns(frame["close_time"])
    observations = [
        MarkObservation(int(timestamp_ns), Decimal(str(value_text)))
        for timestamp_ns, value_text in zip(
            timestamps,
            frame["close"].astype(str),
            strict=True,
        )
    ]
    observations.sort(key=lambda item: item.timestamp_ns)
    if any(item.value <= 0 for item in observations):
        raise ValueError("mark price must be positive")
    if len({item.timestamp_ns for item in observations}) != len(observations):
        raise ValueError("duplicate mark-price close timestamp")
    return observations


def align_preceding_marks(
    funding: list[FundingObservation],
    marks: list[MarkObservation],
    *,
    maximum_age_ns: int = MAX_MARK_AGE_NS,
) -> list[FundingMarkPair]:
    if maximum_age_ns <= 0:
        raise ValueError("maximum mark age must be positive")
    pairs: list[FundingMarkPair] = []
    mark_index = -1
    for funding_item in funding:
        while (
            mark_index + 1 < len(marks)
            and marks[mark_index + 1].timestamp_ns <= funding_item.timestamp_ns
        ):
            mark_index += 1
        if mark_index < 0:
            raise ValueError(f"no preceding mark price for funding {funding_item.timestamp_ns}")
        mark = marks[mark_index]
        age = funding_item.timestamp_ns - mark.timestamp_ns
        if age < 0 or age > maximum_age_ns:
            raise ValueError(
                f"stale mark price for funding {funding_item.timestamp_ns}: age_ns={age}",
            )
        pairs.append(FundingMarkPair(funding_item, mark))
    return pairs


def _month_starts(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end must not precede start")
    current = start.replace(day=1)
    last = end.replace(day=1)
    output: list[date] = []
    while current <= last:
        output.append(current)
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return output


def _verified_monthly_archive(url: str, archive: Path) -> Path:
    checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
    _download(url, archive)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch {archive}: {actual} != {expected}")
    return archive


def _read_funding_archive(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, compression="zip", dtype=str)
    if set(FUNDING_COLUMNS).issubset(frame.columns):
        return frame[list(FUNDING_COLUMNS)]
    raw = pd.read_csv(path, compression="zip", header=None, dtype=str)
    if raw.shape[1] != len(FUNDING_COLUMNS):
        raise RuntimeError(f"unexpected funding archive schema: {raw.shape}")
    raw.columns = FUNDING_COLUMNS
    if raw.iloc[0, 0] == "calc_time":
        raw = raw.iloc[1:].copy()
    return raw


def _read_mark_archive(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None, dtype=str)
    if raw.shape[1] != len(KLINE_COLUMNS):
        with_header = pd.read_csv(path, compression="zip", dtype=str)
        if not set(KLINE_COLUMNS).issubset(with_header.columns):
            raise RuntimeError(f"unexpected mark archive schema: {list(with_header.columns)}")
        return with_header[list(KLINE_COLUMNS)]
    raw.columns = KLINE_COLUMNS
    if not str(raw.iloc[0]["open_time"]).lstrip("-").isdigit():
        raw = raw.iloc[1:].copy()
    return raw


def load_funding_observations(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> list[FundingObservation]:
    rows: list[FundingObservation] = []
    for month in _month_starts(start, end):
        stamp = month.strftime("%Y-%m")
        name = f"{symbol}-fundingRate-{stamp}.zip"
        url = f"{FUNDING_BASE}/{symbol}/{name}"
        archive = _verified_monthly_archive(url, cache / "funding" / symbol / name)
        rows.extend(parse_funding_frame(_read_funding_archive(archive)))
    start_ns = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp() * 1e9)
    end_exclusive_ns = int(
        datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC).timestamp() * 1e9,
    )
    selected = [item for item in rows if start_ns <= item.timestamp_ns <= end_exclusive_ns]
    selected.sort(key=lambda item: item.timestamp_ns)
    if len({item.timestamp_ns for item in selected}) != len(selected):
        raise RuntimeError(f"duplicate funding rows across monthly archives for {symbol}")
    return selected


def load_mark_observations(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> list[MarkObservation]:
    rows: list[MarkObservation] = []
    for month in _month_starts(start - timedelta(days=1), end):
        stamp = month.strftime("%Y-%m")
        name = f"{symbol}-1m-{stamp}.zip"
        url = f"{MARK_BASE}/{symbol}/1m/{name}"
        archive = _verified_monthly_archive(url, cache / "mark_price" / symbol / name)
        rows.extend(parse_mark_frame(_read_mark_archive(archive)))
    start_ns = int(
        datetime.combine(start - timedelta(days=1), datetime.min.time(), tzinfo=UTC).timestamp() * 1e9,
    )
    end_exclusive_ns = int(
        datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC).timestamp() * 1e9,
    )
    selected = [item for item in rows if start_ns <= item.timestamp_ns <= end_exclusive_ns]
    selected.sort(key=lambda item: item.timestamp_ns)
    return selected


def add_symbol_funding_data(
    engine: BacktestEngine,
    symbol: str,
    instrument: object,
    start: date,
    end: date,
    cache: Path,
) -> dict[str, object]:
    funding = load_funding_observations(symbol, start, end, cache)
    marks = load_mark_observations(symbol, start, end, cache)
    pairs = align_preceding_marks(funding, marks)
    mark_events = [
        MarkPriceUpdate(
            instrument_id=instrument.id,
            value=instrument.make_price(pair.mark.value),
            ts_event=pair.mark.timestamp_ns,
            ts_init=pair.mark.timestamp_ns,
        )
        for pair in pairs
    ]
    funding_events = [
        FundingRateUpdate(
            instrument_id=instrument.id,
            rate=pair.funding.rate,
            interval=pair.funding.interval_minutes,
            next_funding_ns=None,
            ts_event=pair.funding.timestamp_ns,
            ts_init=pair.funding.timestamp_ns,
        )
        for pair in pairs
    ]
    engine.add_data(mark_events, sort=False)
    engine.add_data(funding_events, sort=False)
    return {
        "symbol": symbol,
        "funding_updates": len(funding_events),
        "mark_updates": len(mark_events),
        "first_funding_ns": None if not pairs else pairs[0].funding.timestamp_ns,
        "last_funding_ns": None if not pairs else pairs[-1].funding.timestamp_ns,
        "provenance": FUNDING_PROVENANCE,
    }
