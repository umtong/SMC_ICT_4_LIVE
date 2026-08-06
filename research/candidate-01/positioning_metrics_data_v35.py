"""Verified causal loader for official Binance USD-M positioning metrics.

Binance Vision publishes one ZIP and SHA-256 CHECKSUM per UTC day. Each row is
an official five-minute BTCUSDT positioning observation. To avoid relying on
undocumented publication timing, this loader makes a row causally available
only after the full five-minute interval following its timestamp has completed.

This module contains no signal, order, fill, PnL or NAV logic.
"""
from __future__ import annotations

from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from io import TextIOWrapper
from pathlib import Path
from typing import Iterable, Iterator
from zipfile import ZipFile

from aggtrade_data import (
    _download_to_path,
    _expected_checksum,
    _sha256_file,
    utc_days,
)


BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
METRIC_INTERVAL_NS = 5 * 60 * 1_000_000_000
REQUIRED_COLUMNS = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


@dataclass(frozen=True, slots=True)
class PositionMetricDownload:
    symbol: str
    day: str
    url: str
    checksum_url: str
    path: str
    checksum_path: str
    size_bytes: int
    sha256: str
    expected_sha256: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PositionMetric:
    observation_time_ns: int
    available_time_ns: int
    symbol: str
    sum_open_interest: float
    sum_open_interest_value: float
    count_toptrader_long_short_ratio: float | None
    sum_toptrader_long_short_ratio: float | None
    count_long_short_ratio: float | None
    sum_taker_long_short_vol_ratio: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PositionMetricBook:
    """Causally indexed, immutable official positioning observations."""

    def __init__(self, rows: Iterable[PositionMetric]) -> None:
        ordered = sorted(
            rows,
            key=lambda row: (row.available_time_ns, row.observation_time_ns),
        )
        if not ordered:
            raise ValueError("position metric book cannot be empty")
        prior_observation = -1
        prior_available = -1
        for row in ordered:
            if row.observation_time_ns <= prior_observation:
                raise ValueError("position metric observation timestamp regression")
            if row.available_time_ns <= prior_available:
                raise ValueError("position metric availability timestamp regression")
            if row.available_time_ns - row.observation_time_ns != METRIC_INTERVAL_NS:
                raise ValueError("position metric causal delay regression")
            prior_observation = row.observation_time_ns
            prior_available = row.available_time_ns
        self.rows = ordered
        self.available_times = [row.available_time_ns for row in ordered]

    def index_at(self, observed_time_ns: int) -> int | None:
        index = bisect_right(self.available_times, int(observed_time_ns)) - 1
        return index if index >= 0 else None

    def row_at(self, observed_time_ns: int) -> PositionMetric | None:
        index = self.index_at(observed_time_ns)
        return self.rows[index] if index is not None else None


def archive_url(symbol: str, day: date) -> str:
    value = day.isoformat()
    return f"{BASE}/{symbol}/{symbol}-metrics-{value}.zip"


def _download_one(
    *,
    symbol: str,
    day: date,
    cache_dir: Path,
) -> PositionMetricDownload:
    url = archive_url(symbol, day)
    archive_name = url.rsplit("/", 1)[-1]
    destination = cache_dir / symbol / archive_name
    checksum_url = url + ".CHECKSUM"
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    _download_to_path(url, destination)
    _download_to_path(checksum_url, checksum_path)
    expected = _expected_checksum(checksum_path, archive_name)
    actual = _sha256_file(destination)
    if actual != expected:
        destination.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        raise ValueError(
            f"checksum mismatch for {archive_name}: expected {expected}, got {actual}",
        )
    with ZipFile(destination) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {destination}, found {members}")
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"corrupt ZIP member {bad} in {destination}")
    return PositionMetricDownload(
        symbol=symbol,
        day=day.isoformat(),
        url=url,
        checksum_url=checksum_url,
        path=str(destination),
        checksum_path=str(checksum_path),
        size_bytes=destination.stat().st_size,
        sha256=actual,
        expected_sha256=expected,
    )


def download_position_metric_days(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    workers: int = 4,
) -> list[PositionMetricDownload]:
    days = utc_days(start, end)
    if not days:
        return []
    records: list[PositionMetricDownload] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(days)))) as executor:
        futures = {
            executor.submit(
                _download_one,
                symbol=symbol,
                day=day,
                cache_dir=cache_dir,
            ): day
            for day in days
        }
        for future in as_completed(futures):
            records.append(future.result())
    return sorted(records, key=lambda row: row.day)


def _timestamp_to_ns(raw: str) -> int:
    text = raw.strip()
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        instant = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    else:
        instant = instant.astimezone(timezone.utc)
    return int(instant.timestamp() * 1_000_000_000)


def _required_float(
    raw: str | None,
    *,
    field: str,
    path: Path,
    row_number: int,
) -> float:
    text = "" if raw is None else raw.strip()
    if not text:
        raise ValueError(f"{path}:{row_number}: required metric {field} is blank")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(
            f"{path}:{row_number}: invalid metric {field}={text!r}",
        ) from exc


def _optional_float(raw: str | None) -> float | None:
    text = "" if raw is None else raw.strip()
    return float(text) if text else None


def iter_download(record: PositionMetricDownload) -> Iterator[PositionMetric]:
    path = Path(record.path)
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {members}")
        with archive.open(members[0], "r") as raw, TextIOWrapper(
            raw,
            encoding="utf-8-sig",
            newline="",
        ) as text:
            reader = csv.DictReader(text)
            if reader.fieldnames is None:
                raise ValueError(f"missing header in {path}")
            missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
            if missing:
                raise ValueError(f"{path}: missing metric columns {missing}")
            previous_time = -1
            rows = 0
            for row_number, row in enumerate(reader, start=2):
                if not row:
                    continue
                if row["symbol"].strip() != record.symbol:
                    raise ValueError(
                        f"{path}:{row_number}: expected {record.symbol}, got {row['symbol']!r}",
                    )
                observation = _timestamp_to_ns(row["create_time"])
                if observation <= previous_time:
                    raise ValueError(
                        f"{path}:{row_number}: metric timestamp regression or duplicate",
                    )
                previous_time = observation
                rows += 1
                yield PositionMetric(
                    observation_time_ns=observation,
                    available_time_ns=observation + METRIC_INTERVAL_NS,
                    symbol=record.symbol,
                    sum_open_interest=_required_float(
                        row["sum_open_interest"],
                        field="sum_open_interest",
                        path=path,
                        row_number=row_number,
                    ),
                    sum_open_interest_value=_required_float(
                        row["sum_open_interest_value"],
                        field="sum_open_interest_value",
                        path=path,
                        row_number=row_number,
                    ),
                    count_toptrader_long_short_ratio=_optional_float(
                        row["count_toptrader_long_short_ratio"],
                    ),
                    sum_toptrader_long_short_ratio=_optional_float(
                        row["sum_toptrader_long_short_ratio"],
                    ),
                    count_long_short_ratio=_optional_float(
                        row["count_long_short_ratio"],
                    ),
                    sum_taker_long_short_vol_ratio=_optional_float(
                        row["sum_taker_long_short_vol_ratio"],
                    ),
                )
            if rows <= 0:
                raise ValueError(f"no position metrics in {path}")


def iter_downloads(
    records: Iterable[PositionMetricDownload],
) -> Iterator[PositionMetric]:
    previous_time = -1
    for record in sorted(records, key=lambda row: row.day):
        for metric in iter_download(record):
            if metric.observation_time_ns <= previous_time:
                raise ValueError(
                    f"cross-file metric timestamp regression at {record.day}",
                )
            previous_time = metric.observation_time_ns
            yield metric


def load_position_metric_book(
    records: Iterable[PositionMetricDownload],
) -> PositionMetricBook:
    return PositionMetricBook(iter_downloads(records))


__all__ = [
    "METRIC_INTERVAL_NS",
    "PositionMetric",
    "PositionMetricBook",
    "PositionMetricDownload",
    "download_position_metric_days",
    "load_position_metric_book",
]
