"""Reproducible Binance Vision BTCUSDT one-minute data ingestion.

The official UM futures kline files include aggregate taker-buy base volume.  That
allows a causal one-minute order-flow imbalance without pretending that OHLCV alone
contains a full historical order book.  Raw archives stay in the external cache;
only checksums and coverage are written to evidence.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from state_engine import FlowBar


BASE_URL = "https://data.binance.vision/data/futures/um"
ONE_MINUTE_NS = 60_000_000_000


@dataclass(frozen=True, slots=True)
class DataFileRecord:
    source_url: str
    local_path: str
    sha256: str
    bytes: int
    rows: int
    first_observed_ns: int
    last_observed_ns: int


@dataclass(frozen=True, slots=True)
class CoverageReport:
    bars: int
    first_observed_ns: int
    last_observed_ns: int
    duplicate_timestamps: int
    missing_minutes: int
    non_positive_prices: int


def _normalize_epoch_to_ns(value: int) -> int:
    # Binance historical archives have used milliseconds and, for some newer
    # products, microseconds.  Preserve either without relying on the file date.
    if value >= 10**18:
        return value
    if value >= 10**15:
        return value * 1_000
    return value * 1_000_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, *, attempts: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SMC-ICT-4-candidate-09/0.1 reproducible-research"},
            )
            with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as output:
                if int(getattr(response, "status", 200)) != 200:
                    raise RuntimeError(f"unexpected HTTP status {response.status} for {url}")
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if temporary.stat().st_size <= 0:
                raise RuntimeError(f"downloaded empty archive from {url}")
            temporary.replace(destination)
            return
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"could not download {url}: {last_error}")


def _archive_rows(path: Path) -> Iterator[list[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"expected one CSV in {path}, found {members}")
            with archive.open(members[0], "r") as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                yield from csv.reader(text)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid Binance archive {path}") from exc


def parse_archive(path: Path) -> list[FlowBar]:
    bars: list[FlowBar] = []
    for row_number, row in enumerate(_archive_rows(path), start=1):
        if not row or not row[0].strip().lstrip("-").isdigit():
            # Recent archives can include a header; older archives do not.
            continue
        if len(row) < 11:
            raise ValueError(f"{path}:{row_number}: expected at least 11 columns, got {len(row)}")
        open_ns = _normalize_epoch_to_ns(int(row[0]))
        observed_ns = open_ns + ONE_MINUTE_NS
        try:
            bar = FlowBar(
                ts_ns=observed_ns,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                taker_buy_volume=float(row[9]),
                trade_count=int(float(row[8])),
            )
        except (ValueError, IndexError) as exc:
            raise ValueError(f"{path}:{row_number}: malformed kline row") from exc
        bars.append(bar)
    if not bars:
        raise ValueError(f"no kline rows parsed from {path}")
    return bars


class BinanceVisionCache:
    def __init__(self, root: Path, symbol: str = "BTCUSDT", interval: str = "1m"):
        self.root = root.expanduser().resolve()
        self.symbol = symbol
        self.interval = interval

    def daily(self, day: date) -> tuple[list[FlowBar], DataFileRecord]:
        filename = f"{self.symbol}-{self.interval}-{day.isoformat()}.zip"
        url = f"{BASE_URL}/daily/klines/{self.symbol}/{self.interval}/{filename}"
        path = self.root / self.symbol / self.interval / "daily" / filename
        if not path.exists():
            _download(url, path)
        bars = parse_archive(path)
        record = DataFileRecord(
            source_url=url,
            local_path=str(path),
            sha256=_sha256(path),
            bytes=path.stat().st_size,
            rows=len(bars),
            first_observed_ns=bars[0].ts_ns,
            last_observed_ns=bars[-1].ts_ns,
        )
        return bars, record

    def monthly(self, year: int, month: int) -> tuple[list[FlowBar], DataFileRecord]:
        label = f"{year:04d}-{month:02d}"
        filename = f"{self.symbol}-{self.interval}-{label}.zip"
        url = f"{BASE_URL}/monthly/klines/{self.symbol}/{self.interval}/{filename}"
        path = self.root / self.symbol / self.interval / "monthly" / filename
        if not path.exists():
            _download(url, path)
        bars = parse_archive(path)
        record = DataFileRecord(
            source_url=url,
            local_path=str(path),
            sha256=_sha256(path),
            bytes=path.stat().st_size,
            rows=len(bars),
            first_observed_ns=bars[0].ts_ns,
            last_observed_ns=bars[-1].ts_ns,
        )
        return bars, record


def validate_coverage(bars: Iterable[FlowBar]) -> CoverageReport:
    ordered = list(bars)
    if not ordered:
        raise ValueError("cannot validate empty data")
    duplicates = 0
    missing = 0
    non_positive = 0
    prior = -1
    for bar in ordered:
        if min(bar.open, bar.high, bar.low, bar.close) <= 0.0:
            non_positive += 1
        if bar.ts_ns == prior:
            duplicates += 1
        elif prior >= 0 and bar.ts_ns > prior + ONE_MINUTE_NS:
            missing += max(0, (bar.ts_ns - prior) // ONE_MINUTE_NS - 1)
        elif prior >= 0 and bar.ts_ns < prior:
            raise ValueError("data timestamps are not sorted")
        prior = bar.ts_ns
    return CoverageReport(
        bars=len(ordered),
        first_observed_ns=ordered[0].ts_ns,
        last_observed_ns=ordered[-1].ts_ns,
        duplicate_timestamps=duplicates,
        missing_minutes=missing,
        non_positive_prices=non_positive,
    )


def load_fixed_weeks(
    config: Mapping[str, object],
    cache: BinanceVisionCache,
) -> tuple[dict[str, list[FlowBar]], dict[str, object]]:
    weeks: dict[str, list[FlowBar]] = {}
    files: list[DataFileRecord] = []
    coverages: dict[str, CoverageReport] = {}
    for item in config["fixed_gate_weeks_utc"]:  # type: ignore[index]
        spec = dict(item)
        name = str(spec["name"])
        start = date.fromisoformat(str(spec["start"]))
        days = int(spec["days"])
        week_bars: list[FlowBar] = []
        for offset in range(days):
            bars, record = cache.daily(start + timedelta(days=offset))
            week_bars.extend(bars)
            files.append(record)
        week_bars.sort(key=lambda bar: bar.ts_ns)
        coverage = validate_coverage(week_bars)
        if coverage.duplicate_timestamps or coverage.non_positive_prices:
            raise ValueError(f"invalid coverage for {name}: {coverage}")
        weeks[name] = week_bars
        coverages[name] = coverage
    manifest = {
        "source": "Binance Vision UM futures one-minute klines",
        "symbol": cache.symbol,
        "interval": cache.interval,
        "files": [asdict(record) for record in files],
        "coverage": {name: asdict(report) for name, report in coverages.items()},
    }
    return weeks, manifest


def _month_starts(start: date, end_exclusive: date) -> Iterator[tuple[int, int]]:
    cursor = date(start.year, start.month, 1)
    while cursor < end_exclusive:
        yield cursor.year, cursor.month
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)


def load_monthly_range(
    *,
    start: date,
    end_exclusive: date,
    cache: BinanceVisionCache,
) -> tuple[list[FlowBar], dict[str, object]]:
    if end_exclusive <= start:
        raise ValueError("end_exclusive must be after start")
    start_ns = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)
    end_ns = int(datetime.combine(end_exclusive, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)
    all_bars: list[FlowBar] = []
    records: list[DataFileRecord] = []
    for year, month in _month_starts(start, end_exclusive):
        bars, record = cache.monthly(year, month)
        records.append(record)
        all_bars.extend(bar for bar in bars if start_ns < bar.ts_ns <= end_ns)
    all_bars.sort(key=lambda bar: bar.ts_ns)
    coverage = validate_coverage(all_bars)
    if coverage.duplicate_timestamps or coverage.non_positive_prices:
        raise ValueError(f"invalid long-evaluation coverage: {coverage}")
    manifest = {
        "source": "Binance Vision UM futures one-minute monthly klines",
        "symbol": cache.symbol,
        "interval": cache.interval,
        "start": start.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "files": [asdict(record) for record in records],
        "coverage": asdict(coverage),
    }
    return all_bars, manifest


def write_manifest(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
