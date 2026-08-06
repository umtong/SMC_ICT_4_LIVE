"""Checksum-verified Binance USD-M mark-price kline loader for funding and liquidation realism.

The archive is normalized independently of NautilusTrader so schema, timestamp, and completeness
contracts can be tested without the execution runtime. The runner converts each completed one-minute
mark-price close into a native ``MarkPriceUpdate`` before globally sorting all historical data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
from pathlib import Path
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import pandas as pd


KLINE_COLUMNS = [
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
class MarkPriceSource:
    period: str
    url: str
    checksum_url: str
    sha256: str
    size_bytes: int
    rows: int


@dataclass(frozen=True, slots=True)
class LoadedMarkPrices:
    frame: pd.DataFrame
    source_files: tuple[MarkPriceSource, ...]
    quality: dict[str, object]


class MarkPriceDataError(RuntimeError):
    """Raised when a mark-price archive violates the data contract."""


def _month_starts(start: datetime, end: datetime) -> Iterable[datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if end <= start:
        raise ValueError("end must be after start")
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    inclusive_end = end - timedelta(microseconds=1)
    final = datetime(inclusive_end.year, inclusive_end.month, 1, tzinfo=timezone.utc)
    while cursor <= final:
        yield cursor
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1, tzinfo=timezone.utc)


def _download(url: str, *, retries: int = 4, timeout: int = 120) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-08/1.0"})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise MarkPriceDataError(f"download failed after {retries} attempts: {url}: {last_error}")


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_archive(
    cache_dir: Path,
    symbol: str,
    month: datetime,
) -> tuple[Path, MarkPriceSource]:
    period = month.strftime("%Y-%m")
    filename = f"{symbol}-1m-{period}.zip"
    base = (
        "https://data.binance.vision/data/futures/um/monthly/"
        f"markPriceKlines/{symbol}/1m"
    )
    url = f"{base}/{filename}"
    checksum_url = f"{url}.CHECKSUM"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / filename

    checksum_text = _download(checksum_url).decode("utf-8").strip()
    expected = checksum_text.split()[0].lower()
    if len(expected) != 64:
        raise MarkPriceDataError(f"invalid checksum payload for {filename}: {checksum_text!r}")
    if destination.exists() and _sha256_file(destination) != expected:
        destination.unlink()
    if not destination.exists():
        payload = _download(url, timeout=300)
        actual = sha256(payload).hexdigest()
        if actual != expected:
            raise MarkPriceDataError(f"SHA-256 mismatch for {filename}: {actual} != {expected}")
        temporary = destination.with_suffix(".zip.tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    actual = _sha256_file(destination)
    return destination, MarkPriceSource(
        period=period,
        url=url,
        checksum_url=checksum_url,
        sha256=actual,
        size_bytes=destination.stat().st_size,
        rows=0,
    )


def _timestamp_unit(values: pd.Series) -> str:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        raise MarkPriceDataError("mark-price archive contains no numeric close_time values")
    median = float(numeric.iloc[len(numeric) // 2])
    return "us" if median > 100_000_000_000_000 else "ms"


def _read_month(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise MarkPriceDataError(f"expected one CSV in {path.name}, found {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(
        io.BytesIO(payload),
        names=KLINE_COLUMNS,
        header=None,
        low_memory=False,
    )
    if frame.shape[1] != len(KLINE_COLUMNS):
        raise MarkPriceDataError(
            f"mark-price archive exposed {frame.shape[1]} columns, expected {len(KLINE_COLUMNS)}"
        )
    frame["close_time"] = pd.to_numeric(frame["close_time"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close_time", "close"]).copy()
    if frame.empty:
        raise MarkPriceDataError(f"mark-price archive {path.name} contains no valid rows")
    return frame[["close_time", "close"]]


def _normalize_mark_price_frame(
    frame: pd.DataFrame,
    *,
    start: datetime,
    end: datetime,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if end <= start:
        raise ValueError("end must be after start")
    required = {"close_time", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise MarkPriceDataError(f"mark-price frame missing {sorted(missing)}")

    data = frame[["close_time", "close"]].copy()
    source_rows = len(data.index)
    data["close_time"] = pd.to_numeric(data["close_time"], errors="coerce")
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data = data.dropna(subset=["close_time", "close"]).copy()
    invalid_numeric_rows = source_rows - len(data.index)
    if data.empty:
        raise MarkPriceDataError("mark-price frame produced no valid numeric rows")

    unit = _timestamp_unit(data["close_time"])
    data.index = pd.to_datetime(data.pop("close_time").astype("int64"), unit=unit, utc=True)
    data.index.name = "mark_price_time"
    data["mark_price"] = data.pop("close").astype(float)
    data = data.loc[data["mark_price"].gt(0)].sort_index()
    duplicate_rows = int(data.index.duplicated(keep="last").sum())
    if duplicate_rows:
        data = data.loc[~data.index.duplicated(keep="last")].copy()
    data = data.loc[(data.index >= start) & (data.index < end)].copy()
    if data.empty:
        raise MarkPriceDataError(f"no mark-price rows in requested interval {start} to {end}")

    deltas = data.index.to_series().diff().dropna().dt.total_seconds()
    gap_count = int((deltas > 61.0).sum())
    maximum_gap = float(deltas.max()) if not deltas.empty else 0.0
    expected_rows = max(1, int((end - start).total_seconds() // 60))
    missing_ratio = max(0.0, (expected_rows - len(data.index)) / expected_rows)
    if missing_ratio > 0.002:
        raise MarkPriceDataError(
            "mark-price completeness below contract: "
            f"missing_ratio={missing_ratio:.6f}, gaps={gap_count}"
        )
    quality: dict[str, object] = {
        "rows": len(data.index),
        "source_rows": source_rows,
        "expected_rows": expected_rows,
        "missing_ratio": missing_ratio,
        "invalid_numeric_rows_removed": invalid_numeric_rows,
        "duplicate_rows_removed": duplicate_rows,
        "gap_count_over_61_seconds": gap_count,
        "max_gap_seconds": maximum_gap,
        "timestamp_unit_detected": unit,
        "first_mark_price_time": data.index[0].isoformat(),
        "last_mark_price_time": data.index[-1].isoformat(),
    }
    return data[["mark_price"]], quality


def load_official_mark_prices(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> LoadedMarkPrices:
    frames: list[pd.DataFrame] = []
    sources: list[MarkPriceSource] = []
    for month in _month_starts(start, end):
        path, source = _verified_archive(cache_dir / symbol, symbol, month)
        month_frame = _read_month(path)
        frames.append(month_frame)
        sources.append(
            MarkPriceSource(
                period=source.period,
                url=source.url,
                checksum_url=source.checksum_url,
                sha256=source.sha256,
                size_bytes=source.size_bytes,
                rows=len(month_frame.index),
            )
        )
    if not frames:
        raise MarkPriceDataError("no mark-price archive months selected")
    normalized, quality = _normalize_mark_price_frame(
        pd.concat(frames, ignore_index=True),
        start=start,
        end=end,
    )
    return LoadedMarkPrices(
        frame=normalized,
        source_files=tuple(sources),
        quality=quality,
    )
