"""Checksum-verified Binance USD-M data loading for candidate-07."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import pandas as pd

from smc_ict_4.manifest import build_data_manifest, write_data_manifest


KLINE_DAILY_URL = (
    "https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1m/"
    "{symbol}-1m-{stamp}.zip"
)
KLINE_MONTHLY_URL = (
    "https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/"
    "{symbol}-1m-{stamp}.zip"
)
FUNDING_URL = (
    "https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/"
    "{symbol}-fundingRate-{month}.zip"
)
KLINE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote", "ignore",
)


@dataclass(frozen=True, slots=True)
class FundingPoint:
    ts_event_ns: int
    rate: Decimal
    interval_minutes: int


@dataclass(frozen=True, slots=True)
class KlineArchiveRequest:
    cadence: str
    stamp: str

    def __post_init__(self) -> None:
        if self.cadence not in {"daily", "monthly"}:
            raise ValueError(f"unsupported kline archive cadence: {self.cadence}")
        if not self.stamp:
            raise ValueError("kline archive stamp must not be empty")


@dataclass(frozen=True, slots=True)
class LoadedBundle:
    frame: pd.DataFrame
    funding: tuple[FundingPoint, ...]
    data_manifest_path: Path
    archives: tuple[Path, ...]


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_bytes(url: str, attempts: int = 5) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-07/1.0"})
            with urlopen(request, timeout=120) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {attempts} attempts: {url}") from last_error


def _ensure_checked_archive(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = _fetch_bytes(url + ".CHECKSUM").decode("utf-8").strip().split()[0].lower()
    if destination.is_file() and sha256_file(destination).lower() == expected:
        return destination
    payload = _fetch_bytes(url)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(payload)
    actual = sha256_file(temporary).lower()
    if actual != expected:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {url}: {actual} != {expected}")
    temporary.replace(destination)
    return destination


def _days(start: date, end_exclusive: date) -> Iterable[date]:
    cursor = start
    while cursor < end_exclusive:
        yield cursor
        cursor += timedelta(days=1)


def _months(start: date, end_exclusive: date) -> Iterable[str]:
    year, month = start.year, start.month
    final = end_exclusive - timedelta(days=1)
    while (year, month) <= (final.year, final.month):
        yield f"{year:04d}-{month:02d}"
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _kline_archive_requests(
    start: date,
    end_exclusive: date,
) -> tuple[KlineArchiveRequest, ...]:
    """Use official monthly archives for complete months, daily at boundaries.

    This changes only transfer granularity. The returned bars are filtered to
    the same exact nanosecond interval after parsing, and every archive is still
    verified against Binance Vision's published SHA-256 checksum.
    """
    if end_exclusive <= start:
        raise ValueError("end_exclusive must follow start")
    requests: list[KlineArchiveRequest] = []
    cursor = start
    while cursor < end_exclusive:
        month_start = date(cursor.year, cursor.month, 1)
        next_month = _next_month(cursor)
        if cursor == month_start and next_month <= end_exclusive:
            requests.append(
                KlineArchiveRequest(
                    cadence="monthly",
                    stamp=f"{cursor.year:04d}-{cursor.month:02d}",
                )
            )
            cursor = next_month
        else:
            requests.append(
                KlineArchiveRequest(
                    cadence="daily",
                    stamp=cursor.isoformat(),
                )
            )
            cursor += timedelta(days=1)
    return tuple(requests)


def _timestamp_ns(value: str | int | float) -> int:
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        numeric = int(Decimal(text))
    except Exception:
        timestamp = pd.Timestamp(text)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return int(timestamp.value)
    magnitude = abs(numeric)
    if magnitude >= 10**18:
        return numeric
    if magnitude >= 10**15:
        return numeric * 1_000
    if magnitude >= 10**12:
        return numeric * 1_000_000
    if magnitude >= 10**9:
        return numeric * 1_000_000_000
    raise ValueError(f"timestamp magnitude is unsupported: {value}")


def _read_kline_archive(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected one CSV in {path}, found {names}")
        with archive.open(names[0]) as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.reader(stream)
            for row in reader:
                if not row:
                    continue
                if row[0].strip().lower() in {"open_time", "open time"}:
                    continue
                if len(row) < len(KLINE_COLUMNS):
                    raise RuntimeError(f"short kline row in {path}: {row[:4]}")
                records.append(dict(zip(KLINE_COLUMNS, row, strict=False)))
    return records


def _read_funding_archive(path: Path) -> list[FundingPoint]:
    points: list[FundingPoint] = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected one funding CSV in {path}, found {names}")
        with archive.open(names[0]) as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            rows = list(csv.reader(stream))
    if not rows:
        return points

    header: list[str] | None = None
    first = [cell.strip().lower() for cell in rows[0]]
    if first and any(any(ch.isalpha() for ch in cell) for cell in first):
        header = first
        data_rows = rows[1:]
    else:
        data_rows = rows

    for row in data_rows:
        if not row:
            continue
        if header is not None:
            values = {name: row[index].strip() for index, name in enumerate(header) if index < len(row)}
            ts_value = next(
                (values[name] for name in ("calc_time", "fundingtime", "funding_time", "time", "timestamp") if name in values),
                row[0],
            )
            rate_value = next(
                (values[name] for name in ("last_funding_rate", "fundingrate", "funding_rate", "rate") if name in values),
                row[-1],
            )
            interval_value = next(
                (values[name] for name in ("funding_interval_hours", "interval_hours", "interval") if name in values),
                "8",
            )
        else:
            ts_value = row[0]
            rate_value = row[-1]
            interval_value = row[1] if len(row) >= 3 else "8"
        try:
            ts_ns = _timestamp_ns(ts_value)
            rate = Decimal(rate_value)
            interval_number = int(Decimal(interval_value))
            interval_minutes = interval_number * 60 if interval_number <= 24 else interval_number
        except Exception as exc:
            raise RuntimeError(f"cannot parse funding row in {path}: {row}") from exc
        points.append(FundingPoint(ts_event_ns=ts_ns, rate=rate, interval_minutes=interval_minutes))
    return points


def load_bundle(
    *,
    symbol: str,
    trade_start: date,
    trade_end: date,
    warmup_days: int,
    cache_root: Path,
    manifest_destination: Path,
) -> LoadedBundle:
    """Download and load one bounded BTC-first evaluation interval."""
    if trade_end <= trade_start:
        raise ValueError("trade_end must follow trade_start")
    if warmup_days < 1:
        raise ValueError("warmup_days must be positive")
    symbol = symbol.upper()
    load_start = trade_start - timedelta(days=warmup_days)
    data_root = cache_root.resolve() / symbol
    kline_root = data_root / "klines-1m"
    funding_root = data_root / "funding-rate"
    archives: list[Path] = []
    rows: list[dict[str, str]] = []
    archive_counts = {"daily": 0, "monthly": 0}

    for request in _kline_archive_requests(load_start, trade_end):
        if request.cadence == "monthly":
            url = KLINE_MONTHLY_URL.format(symbol=symbol, stamp=request.stamp)
        else:
            url = KLINE_DAILY_URL.format(symbol=symbol, stamp=request.stamp)
        destination = kline_root / f"{symbol}-1m-{request.stamp}.zip"
        archive = _ensure_checked_archive(url, destination)
        archives.append(archive)
        archive_counts[request.cadence] += 1
        rows.extend(_read_kline_archive(archive))

    funding_points: list[FundingPoint] = []
    for month in _months(load_start, trade_end):
        url = FUNDING_URL.format(symbol=symbol, month=month)
        destination = funding_root / f"{symbol}-fundingRate-{month}.zip"
        archive = _ensure_checked_archive(url, destination)
        archives.append(archive)
        funding_points.extend(_read_funding_archive(archive))

    if not rows:
        raise RuntimeError("no kline rows loaded")
    frame = pd.DataFrame.from_records(rows)
    for name in ("open", "high", "low", "close", "volume"):
        frame[name] = pd.to_numeric(frame[name], errors="raise")
    frame["close_time_ns"] = frame["close_time"].map(_timestamp_ns)
    frame = frame.sort_values("close_time_ns", kind="stable")
    frame = frame.drop_duplicates(subset=["close_time_ns"], keep="last")
    load_start_ns = int(datetime.combine(load_start, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)
    trade_end_ns = int(datetime.combine(trade_end, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)
    frame = frame[(frame["close_time_ns"] >= load_start_ns) & (frame["close_time_ns"] < trade_end_ns)]
    frame.index = pd.to_datetime(frame["close_time_ns"], unit="ns", utc=True)
    frame = frame[["open", "high", "low", "close", "volume"]].copy()
    frame.index.name = "timestamp"

    funding = tuple(
        sorted(
            (point for point in funding_points if load_start_ns <= point.ts_event_ns < trade_end_ns),
            key=lambda item: item.ts_event_ns,
        )
    )
    manifest = build_data_manifest(
        data_root,
        dataset="binance-usdm-public-1m-klines-and-funding",
        include=archives,
        metadata_values={
            "symbol": symbol,
            "load_start": load_start.isoformat(),
            "trade_start": trade_start.isoformat(),
            "trade_end_exclusive": trade_end.isoformat(),
            "kline_rows": int(len(frame.index)),
            "daily_kline_archives": archive_counts["daily"],
            "monthly_kline_archives": archive_counts["monthly"],
            "funding_points": len(funding),
            "source": "Binance Vision public data",
        },
    )
    write_data_manifest(manifest_destination, manifest)
    return LoadedBundle(frame=frame, funding=funding, data_manifest_path=manifest_destination, archives=tuple(archives))


def write_bundle_summary(path: Path, bundle: LoadedBundle) -> None:
    payload = {
        "rows": int(len(bundle.frame.index)),
        "first_timestamp": bundle.frame.index[0].isoformat(),
        "last_timestamp": bundle.frame.index[-1].isoformat(),
        "funding_points": [
            {"ts_event_ns": item.ts_event_ns, "rate": str(item.rate), "interval_minutes": item.interval_minutes}
            for item in bundle.funding
        ],
        "archives": [item.as_posix() for item in bundle.archives],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
