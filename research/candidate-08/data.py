"""Official Binance Vision USD-M futures kline loader with checksum verification."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import pandas as pd

from nautilus_trader.model.data import Bar
from nautilus_trader.model.objects import Price, Quantity


BINANCE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]


@dataclass(frozen=True, slots=True)
class SourceFile:
    period: str
    url: str
    checksum_url: str
    sha256: str
    size_bytes: int
    rows: int


@dataclass(frozen=True, slots=True)
class LoadedBars:
    bars: list[Any]
    frame: pd.DataFrame
    source_files: tuple[SourceFile, ...]
    quality: dict[str, Any]


class BinanceDataError(RuntimeError):
    pass


def _month_starts(start: datetime, end: datetime) -> Iterable[datetime]:
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    final = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    while cursor <= final:
        yield cursor
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1, tzinfo=timezone.utc)


def _download(url: str, *, retries: int = 4, timeout: int = 90) -> bytes:
    error: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-08/1.0"})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise BinanceDataError(f"download failed after {retries} attempts: {url}: {error}")


def _verified_zip(cache_dir: Path, symbol: str, interval: str, month: datetime) -> tuple[Path, SourceFile]:
    period = month.strftime("%Y-%m")
    filename = f"{symbol}-{interval}-{period}.zip"
    base = f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/{interval}"
    url = f"{base}/{filename}"
    checksum_url = f"{url}.CHECKSUM"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / filename

    checksum_text = _download(checksum_url).decode("utf-8").strip()
    expected = checksum_text.split()[0].lower()
    if len(expected) != 64:
        raise BinanceDataError(f"invalid checksum payload for {filename}: {checksum_text!r}")

    if destination.exists():
        actual = _sha256_file(destination)
        if actual != expected:
            destination.unlink()

    if not destination.exists():
        payload = _download(url)
        actual = sha256(payload).hexdigest()
        if actual != expected:
            raise BinanceDataError(f"SHA-256 mismatch for {filename}: {actual} != {expected}")
        temporary = destination.with_suffix(".zip.tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)

    actual = _sha256_file(destination)
    return destination, SourceFile(
        period=period,
        url=url,
        checksum_url=checksum_url,
        sha256=actual,
        size_bytes=destination.stat().st_size,
        rows=0,
    )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_month(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise BinanceDataError(f"expected one CSV in {path}, found {members}")
        with archive.open(members[0]) as raw:
            payload = raw.read()
    frame = pd.read_csv(
        io.BytesIO(payload),
        names=BINANCE_COLUMNS,
        header=None,
        low_memory=False,
    )
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="coerce")
    frame = frame.loc[frame["open_time"].notna()].copy()
    for column in ("open", "high", "low", "close", "volume", "close_time"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close", "volume", "close_time"])
    return frame


def _timestamp_unit(series: pd.Series) -> str:
    value = float(series.dropna().iloc[len(series.dropna()) // 2])
    return "us" if value > 100_000_000_000_000 else "ms"


def load_official_binance_bars(
    *,
    symbol: str,
    interval: str,
    load_start: datetime,
    load_end: datetime,
    bar_type: Any,
    instrument: Any,
    cache_dir: str | Path,
) -> LoadedBars:
    if load_start.tzinfo is None or load_end.tzinfo is None:
        raise ValueError("load_start and load_end must be timezone-aware")
    if load_end <= load_start:
        raise ValueError("load_end must be after load_start")

    cache = Path(cache_dir)
    frames: list[pd.DataFrame] = []
    sources: list[SourceFile] = []
    for month in _month_starts(load_start, load_end):
        archive_path, source = _verified_zip(cache, symbol, interval, month)
        month_frame = _read_month(archive_path)
        frames.append(month_frame)
        sources.append(
            SourceFile(
                period=source.period,
                url=source.url,
                checksum_url=source.checksum_url,
                sha256=source.sha256,
                size_bytes=source.size_bytes,
                rows=len(month_frame.index),
            )
        )

    if not frames:
        raise BinanceDataError("no monthly files selected")
    frame = pd.concat(frames, ignore_index=True)
    unit = _timestamp_unit(frame["close_time"])
    observed_index = pd.to_datetime(frame["close_time"], unit=unit, utc=True)
    frame.index = observed_index
    frame.index.name = "observed_time"
    frame = frame.loc[(frame.index >= load_start) & (frame.index < load_end)].copy()
    frame = frame.sort_index()
    duplicate_rows = int(frame.index.duplicated(keep="last").sum())
    if duplicate_rows:
        frame = frame.loc[~frame.index.duplicated(keep="last")]

    if frame.empty:
        raise BinanceDataError(f"no rows in requested interval {load_start} to {load_end}")
    invalid_ohlc = int(
        (
            (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["low"] > frame[["open", "close"]].min(axis=1))
            | (frame["low"] > frame["high"])
            | (frame["volume"] < 0)
        ).sum()
    )
    if invalid_ohlc:
        raise BinanceDataError(f"invalid OHLC rows: {invalid_ohlc}")

    deltas = frame.index.to_series().diff().dropna().dt.total_seconds()
    gap_count = int((deltas > 61.0).sum())
    max_gap_seconds = float(deltas.max()) if not deltas.empty else 0.0
    expected_rows = max(1, int((load_end - load_start).total_seconds() // 60))
    missing_ratio = max(0.0, (expected_rows - len(frame.index)) / expected_rows)
    if missing_ratio > 0.002:
        raise BinanceDataError(
            f"data completeness below contract: missing_ratio={missing_ratio:.6f}, gaps={gap_count}"
        )

    # Build official Nautilus ``Bar`` objects directly. Pandas 3 copy-on-write can
    # expose ``DataFrame.values`` as read-only, while the pinned Cython wrangler
    # requests a writable memoryview. Direct construction preserves the exact same
    # Nautilus data type and timestamp semantics without a compatibility-dependent
    # pandas buffer hand-off.
    values = frame[["open", "high", "low", "close", "volume"]].to_numpy(
        dtype="float64",
        copy=True,
    )
    timestamps_ns = frame.index.asi8
    bars = [
        Bar(
            bar_type=bar_type,
            open=Price(float(row[0]), instrument.price_precision),
            high=Price(float(row[1]), instrument.price_precision),
            low=Price(float(row[2]), instrument.price_precision),
            close=Price(float(row[3]), instrument.price_precision),
            volume=Quantity(float(row[4]), instrument.size_precision),
            ts_event=int(timestamp_ns),
            ts_init=int(timestamp_ns),
        )
        for row, timestamp_ns in zip(values, timestamps_ns, strict=True)
    ]
    quality = {
        "rows": len(frame.index),
        "expected_rows": expected_rows,
        "missing_ratio": missing_ratio,
        "duplicate_rows_removed": duplicate_rows,
        "gap_count_over_61_seconds": gap_count,
        "max_gap_seconds": max_gap_seconds,
        "timestamp_unit_detected": unit,
        "first_observed_time": frame.index[0].isoformat(),
        "last_observed_time": frame.index[-1].isoformat(),
    }
    return LoadedBars(
        bars=bars,
        frame=frame,
        source_files=tuple(sources),
        quality=quality,
    )
