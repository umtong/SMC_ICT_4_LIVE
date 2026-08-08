"""Official Binance USD-M one-minute kline plus bookDepth ingestion.

The last complete depth snapshot in a UTC minute becomes usable only at the next minute
boundary. It is never backfilled into its source minute. A bounded freshness rule prevents
stale visible liquidity from being carried indefinitely.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from state_engine import FlowBar, MINUTE_NS

BASE = "https://data.binance.vision/data/futures/um"
MAX_DEPTH_AGE_NS = 180 * 1_000_000_000


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
    missing_depth_bars: int


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    available_ns: int
    observed_ns: int
    bid_depth: float
    ask_depth: float
    bid_notional: float
    ask_notional: float


def _normalize_epoch_to_ns(value: int) -> int:
    if value >= 10**18:
        return value
    if value >= 10**15:
        return value * 1_000
    return value * 1_000_000


def _parse_timestamp_ns(raw: str) -> int:
    text = raw.strip()
    try:
        return _normalize_epoch_to_ns(int(float(text)))
    except ValueError:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return int(stamp.timestamp() * 1_000_000_000)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, *, attempts: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    partial = destination.with_suffix(destination.suffix + ".partial")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "candidate-09-v29-depth/1.0"},
            )
            with urllib.request.urlopen(request, timeout=180) as response, partial.open(
                "wb"
            ) as output:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(chunk)
            partial.replace(destination)
            return
        except Exception as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed: {url}") from last_error


def _csv_rows(path: Path):
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in {path}, found {names}")
        with archive.open(names[0]) as raw:
            yield from csv.reader(
                io.TextIOWrapper(raw, encoding="utf-8", newline="")
            )


def parse_kline_archive(
    path: Path,
) -> list[tuple[int, float, float, float, float, float, float, int]]:
    rows = []
    for row in _csv_rows(path):
        if not row or not row[0].strip().isdigit():
            continue
        rows.append(
            (
                _normalize_epoch_to_ns(int(row[0])) + MINUTE_NS,
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
                float(row[9]),
                int(float(row[8])),
            )
        )
    return rows


def parse_depth_archive(path: Path) -> list[DepthSnapshot]:
    iterator = iter(_csv_rows(path))
    first = next(iterator, None)
    if first is None:
        return []
    header = [value.strip().lower() for value in first]
    required = {"timestamp", "percentage", "depth", "notional"}
    if not required.issubset(header):
        raise ValueError(f"unexpected bookDepth schema in {path}: {header}")
    ti, pi, di, ni = (
        header.index(name)
        for name in ("timestamp", "percentage", "depth", "notional")
    )

    by_timestamp: dict[int, dict[int, tuple[float, float]]] = {}
    for row in iterator:
        if len(row) <= max(ti, pi, di, ni):
            continue
        observed_ns = _parse_timestamp_ns(row[ti])
        percentage = int(float(row[pi]))
        if percentage not in {-1, 1}:
            continue
        depth = float(row[di])
        notional = float(row[ni])
        by_timestamp.setdefault(observed_ns, {})[percentage] = (depth, notional)

    last_by_available_minute: dict[int, DepthSnapshot] = {}
    for observed_ns, bands in by_timestamp.items():
        if -1 not in bands or 1 not in bands:
            continue
        available_ns = ((observed_ns // MINUTE_NS) + 1) * MINUTE_NS
        bid_depth, bid_notional = bands[-1]
        ask_depth, ask_notional = bands[1]
        snapshot = DepthSnapshot(
            available_ns,
            observed_ns,
            bid_depth,
            ask_depth,
            bid_notional,
            ask_notional,
        )
        prior = last_by_available_minute.get(available_ns)
        if prior is None or snapshot.observed_ns > prior.observed_ns:
            last_by_available_minute[available_ns] = snapshot
    return [
        last_by_available_minute[key]
        for key in sorted(last_by_available_minute)
    ]


class BinanceVisionCache:
    def __init__(
        self,
        root: Path,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
    ):
        self.root = Path(root).resolve()
        self.symbol = symbol
        self.interval = interval

    def _archive(
        self,
        kind: str,
        label: str,
        *,
        monthly: bool,
        interval: str | None = None,
    ) -> tuple[str, Path]:
        scope = "monthly" if monthly else "daily"
        if kind == "klines":
            assert interval
            filename = f"{self.symbol}-{interval}-{label}.zip"
            url = (
                f"{BASE}/{scope}/klines/{self.symbol}/{interval}/{filename}"
            )
            path = (
                self.root
                / self.symbol
                / "klines"
                / interval
                / scope
                / filename
            )
        else:
            filename = f"{self.symbol}-bookDepth-{label}.zip"
            url = f"{BASE}/{scope}/bookDepth/{self.symbol}/{filename}"
            path = self.root / self.symbol / "bookDepth" / scope / filename
        _download(url, path)
        return url, path

    def month(
        self,
        year: int,
        month: int,
    ) -> tuple[list[FlowBar], list[DataFileRecord]]:
        label = f"{year:04d}-{month:02d}"
        kline_url, kline_path = self._archive(
            "klines",
            label,
            monthly=True,
            interval=self.interval,
        )
        try:
            depth_url, depth_path = self._archive(
                "bookDepth",
                label,
                monthly=True,
            )
            depth_sources = [(depth_url, depth_path)]
        except RuntimeError:
            start = date(year, month, 1)
            end = date(
                year + (month == 12),
                1 if month == 12 else month + 1,
                1,
            )
            depth_sources = []
            current = start
            while current < end:
                depth_sources.append(
                    self._archive(
                        "bookDepth",
                        current.isoformat(),
                        monthly=False,
                    )
                )
                current += timedelta(days=1)

        parsed_depth_by_path: dict[Path, list[DepthSnapshot]] = {}
        depth_snapshots: list[DepthSnapshot] = []
        for _, path in depth_sources:
            parsed = parse_depth_archive(path)
            parsed_depth_by_path[path] = parsed
            depth_snapshots.extend(parsed)
        depth_snapshots.sort(key=lambda item: item.available_ns)

        klines = parse_kline_archive(kline_path)
        bars: list[FlowBar] = []
        index = 0
        active: DepthSnapshot | None = None
        for ts_ns, o, h, l, c, volume, taker_buy, count in klines:
            while (
                index < len(depth_snapshots)
                and depth_snapshots[index].available_ns <= ts_ns
            ):
                active = depth_snapshots[index]
                index += 1
            if active is None or ts_ns - active.observed_ns > MAX_DEPTH_AGE_NS:
                values = (None, None, None, None, None)
            else:
                values = (
                    active.bid_depth,
                    active.ask_depth,
                    active.bid_notional,
                    active.ask_notional,
                    active.observed_ns,
                )
            bars.append(
                FlowBar(
                    ts_ns,
                    o,
                    h,
                    l,
                    c,
                    volume,
                    taker_buy,
                    count,
                    *values,
                )
            )

        records = [
            DataFileRecord(
                kline_url,
                str(kline_path),
                _sha256(kline_path),
                kline_path.stat().st_size,
                len(klines),
                klines[0][0],
                klines[-1][0],
            )
        ]
        for url, path in depth_sources:
            parsed = parsed_depth_by_path[path]
            records.append(
                DataFileRecord(
                    url,
                    str(path),
                    _sha256(path),
                    path.stat().st_size,
                    len(parsed),
                    parsed[0].observed_ns if parsed else 0,
                    parsed[-1].observed_ns if parsed else 0,
                )
            )
        return bars, records


def validate_coverage(bars: Iterable[FlowBar]) -> CoverageReport:
    materialized = list(bars)
    if not materialized:
        raise ValueError("no bars")
    duplicate = 0
    missing = 0
    invalid = 0
    missing_depth = 0
    previous = -1
    for bar in materialized:
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            invalid += 1
        if bar.bid_notional is None or bar.ask_notional is None:
            missing_depth += 1
        if bar.ts_ns == previous:
            duplicate += 1
        elif previous >= 0 and bar.ts_ns > previous + MINUTE_NS:
            missing += (bar.ts_ns - previous) // MINUTE_NS - 1
        elif previous >= 0 and bar.ts_ns < previous:
            raise ValueError("bars are not sorted")
        previous = bar.ts_ns
    return CoverageReport(
        len(materialized),
        materialized[0].ts_ns,
        materialized[-1].ts_ns,
        duplicate,
        missing,
        invalid,
        missing_depth,
    )


def _month_starts(start: date, end_exclusive: date):
    current = date(start.year, start.month, 1)
    while current < end_exclusive:
        yield current.year, current.month
        current = date(
            current.year + (current.month == 12),
            1 if current.month == 12 else current.month + 1,
            1,
        )


def _load_range(
    start: date,
    end_exclusive: date,
    cache: BinanceVisionCache,
):
    lower = int(
        datetime.combine(
            start,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )
    upper = int(
        datetime.combine(
            end_exclusive,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )
    bars: list[FlowBar] = []
    records: list[DataFileRecord] = []
    for year, month in _month_starts(start, end_exclusive):
        month_bars, month_records = cache.month(year, month)
        bars.extend(bar for bar in month_bars if lower < bar.ts_ns <= upper)
        records.extend(month_records)
    bars.sort(key=lambda item: item.ts_ns)
    coverage = validate_coverage(bars)
    if coverage.duplicate_timestamps or coverage.non_positive_prices:
        raise ValueError(f"invalid coverage: {coverage}")
    return bars, records, coverage


def load_fixed_weeks(
    config: Mapping[str, object],
    cache: BinanceVisionCache,
):
    output = {}
    files: list[DataFileRecord] = []
    coverage = {}
    for raw in config["fixed_gate_weeks_utc"]:
        item = dict(raw)
        start = date.fromisoformat(str(item["start"]))
        end = start + timedelta(days=int(item["days"]))
        bars, records, report = _load_range(start, end, cache)
        output[str(item["name"])] = bars
        files.extend(records)
        coverage[str(item["name"])] = asdict(report)
    return output, {
        "source": (
            "Binance Vision USD-M monthly klines plus "
            "monthly/daily bookDepth"
        ),
        "files": [asdict(record) for record in files],
        "coverage": coverage,
    }


def load_monthly_range(
    *,
    start: date,
    end_exclusive: date,
    cache: BinanceVisionCache,
):
    bars, records, coverage = _load_range(start, end_exclusive, cache)
    return bars, {
        "source": (
            "Binance Vision USD-M monthly klines plus "
            "monthly/daily bookDepth"
        ),
        "start": start.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "files": [asdict(record) for record in records],
        "coverage": asdict(coverage),
    }


def write_manifest(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
