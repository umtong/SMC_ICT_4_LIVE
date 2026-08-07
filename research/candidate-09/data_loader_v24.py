"""Causal Binance Vision USD-M futures, index-price and positioning ingestion.

The traded BTCUSDT perpetual kline and the Binance BTCUSDT index-price kline are joined
only after the same one-minute interval has completed.  Five-minute metrics become
available one completed minute after ``create_time``.  This deliberately prevents the
researcher from ordering index, price and positioning updates in the strategy's favor.
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
from typing import Iterable, Iterator, Mapping, Sequence

from state_engine import FlowBar


BASE_URL = "https://data.binance.vision/data/futures/um"
ONE_MINUTE_NS = 60_000_000_000


@dataclass(frozen=True, slots=True)
class DataFileRecord:
    data_type: str
    source_url: str
    local_path: str
    sha256: str
    bytes: int
    rows: int
    first_observed_ns: int
    last_observed_ns: int


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    create_ns: int
    available_ns: int
    symbol: str
    open_interest: float
    open_interest_value: float | None
    top_trader_account_ratio: float | None
    top_trader_position_ratio: float | None
    global_account_ratio: float | None
    taker_ratio: float | None


@dataclass(frozen=True, slots=True)
class IndexBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class CoverageReport:
    bars: int
    first_observed_ns: int
    last_observed_ns: int
    duplicate_timestamps: int
    missing_minutes: int
    non_positive_prices: int
    metric_updates: int
    bars_without_metric: int
    maximum_metric_age_minutes: float | None
    bars_without_index: int


def _normalize_epoch_to_ns(value: int) -> int:
    if value >= 10**18:
        return value
    if value >= 10**15:
        return value * 1_000
    if value >= 10**12:
        return value * 1_000_000
    return value * 1_000_000_000


def _parse_time_ns(value: str) -> int:
    stripped = value.strip()
    if stripped.lstrip("-").isdigit():
        return _normalize_epoch_to_ns(int(stripped))
    parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


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
                headers={"User-Agent": "SMC-ICT-4-candidate-09-v24 reproducible-research"},
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
        except urllib.error.HTTPError as exc:
            temporary.unlink(missing_ok=True)
            if exc.code == 404:
                raise FileNotFoundError(url) from exc
            last_error = exc
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
        if attempt + 1 < attempts:
            time.sleep(2**attempt)
    raise RuntimeError(f"could not download {url}: {last_error}")


def _csv_member(path: Path) -> tuple[zipfile.ZipFile, str]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid Binance archive {path}") from exc
    members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(members) != 1:
        archive.close()
        raise ValueError(f"expected one CSV in {path}, found {members}")
    return archive, members[0]


def parse_kline_archive(path: Path) -> list[FlowBar]:
    bars: list[FlowBar] = []
    archive, member = _csv_member(path)
    try:
        with archive.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            for row_number, row in enumerate(csv.reader(text), start=1):
                if not row or not row[0].strip().lstrip("-").isdigit():
                    continue
                if len(row) < 11:
                    raise ValueError(f"{path}:{row_number}: expected at least 11 columns, got {len(row)}")
                open_ns = _normalize_epoch_to_ns(int(row[0]))
                try:
                    bars.append(FlowBar(
                        ts_ns=open_ns + ONE_MINUTE_NS,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                        taker_buy_volume=float(row[9]),
                        trade_count=int(float(row[8])),
                    ))
                except (ValueError, IndexError) as exc:
                    raise ValueError(f"{path}:{row_number}: malformed futures kline row") from exc
    finally:
        archive.close()
    if not bars:
        raise ValueError(f"no futures kline rows parsed from {path}")
    return bars


def parse_index_archive(path: Path) -> list[IndexBar]:
    bars: list[IndexBar] = []
    archive, member = _csv_member(path)
    try:
        with archive.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            for row_number, row in enumerate(csv.reader(text), start=1):
                if not row or not row[0].strip().lstrip("-").isdigit():
                    continue
                if len(row) < 5:
                    raise ValueError(f"{path}:{row_number}: expected at least 5 columns, got {len(row)}")
                open_ns = _normalize_epoch_to_ns(int(row[0]))
                try:
                    item = IndexBar(
                        ts_ns=open_ns + ONE_MINUTE_NS,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                    )
                except (ValueError, IndexError) as exc:
                    raise ValueError(f"{path}:{row_number}: malformed index-price kline row") from exc
                if min(item.open, item.high, item.low, item.close) <= 0.0:
                    raise ValueError(f"{path}:{row_number}: non-positive index price")
                bars.append(item)
    finally:
        archive.close()
    if not bars:
        raise ValueError(f"no index-price rows parsed from {path}")
    return bars


def _optional_float(row: Mapping[str, str], key: str) -> float | None:
    value = row.get(key)
    if value is None or not str(value).strip():
        return None
    return float(value)


def parse_metric_archive(path: Path, *, expected_symbol: str) -> list[MetricSnapshot]:
    archive, member = _csv_member(path)
    snapshots: list[MetricSnapshot] = []
    try:
        with archive.open(member, "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            rows = list(csv.reader(text))
    finally:
        archive.close()
    if not rows:
        raise ValueError(f"no metric rows parsed from {path}")
    header = [item.strip() for item in rows[0]]
    has_header = "create_time" in header
    if has_header:
        dict_rows: Iterable[Mapping[str, str]] = (
            dict(zip(header, row, strict=False)) for row in rows[1:] if row
        )
    else:
        names = [
            "create_time",
            "symbol",
            "sum_open_interest",
            "sum_open_interest_value",
            "count_toptrader_long_short_ratio",
            "sum_toptrader_long_short_ratio",
            "count_long_short_ratio",
            "sum_taker_long_short_vol_ratio",
        ]
        dict_rows = (dict(zip(names, row, strict=False)) for row in rows if row)
    for row_number, row in enumerate(dict_rows, start=2 if has_header else 1):
        try:
            symbol = str(row.get("symbol", expected_symbol)).strip() or expected_symbol
            if symbol != expected_symbol:
                continue
            create_ns = _parse_time_ns(str(row["create_time"]))
            snapshots.append(MetricSnapshot(
                create_ns=create_ns,
                available_ns=create_ns + ONE_MINUTE_NS,
                symbol=symbol,
                open_interest=float(row["sum_open_interest"]),
                open_interest_value=_optional_float(row, "sum_open_interest_value"),
                top_trader_account_ratio=_optional_float(row, "count_toptrader_long_short_ratio"),
                top_trader_position_ratio=_optional_float(row, "sum_toptrader_long_short_ratio"),
                global_account_ratio=_optional_float(row, "count_long_short_ratio"),
                taker_ratio=_optional_float(row, "sum_taker_long_short_vol_ratio"),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}:{row_number}: malformed metrics row") from exc
    snapshots.sort(key=lambda item: item.available_ns)
    if not snapshots:
        raise ValueError(f"no usable {expected_symbol} metric rows parsed from {path}")
    for prior, current in zip(snapshots, snapshots[1:]):
        if current.available_ns <= prior.available_ns:
            raise ValueError(f"metrics timestamps are not strictly increasing in {path}")
    return snapshots


def enrich_bars(
    bars: Sequence[FlowBar],
    metrics: Sequence[MetricSnapshot],
    index_bars: Sequence[IndexBar],
) -> list[FlowBar]:
    enriched: list[FlowBar] = []
    metric_index = 0
    active: MetricSnapshot | None = None
    ordered_metrics = sorted(metrics, key=lambda item: item.available_ns)
    index_by_time = {item.ts_ns: item for item in index_bars}
    for bar in bars:
        while metric_index < len(ordered_metrics) and ordered_metrics[metric_index].available_ns <= bar.ts_ns:
            active = ordered_metrics[metric_index]
            metric_index += 1
        index = index_by_time.get(bar.ts_ns)
        enriched.append(FlowBar(
            ts_ns=bar.ts_ns,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            taker_buy_volume=bar.taker_buy_volume,
            trade_count=bar.trade_count,
            index_open=index.open if index is not None else None,
            index_high=index.high if index is not None else None,
            index_low=index.low if index is not None else None,
            index_close=index.close if index is not None else None,
            metric_observed_ns=active.available_ns if active is not None else None,
            open_interest=active.open_interest if active is not None else None,
            open_interest_value=active.open_interest_value if active is not None else None,
            metric_taker_ratio=active.taker_ratio if active is not None else None,
            top_trader_account_ratio=active.top_trader_account_ratio if active is not None else None,
            top_trader_position_ratio=active.top_trader_position_ratio if active is not None else None,
            global_account_ratio=active.global_account_ratio if active is not None else None,
        ))
    return enriched


class BinanceVisionCache:
    def __init__(self, root: Path, symbol: str = "BTCUSDT", interval: str = "1m"):
        self.root = root.expanduser().resolve()
        self.symbol = symbol
        self.interval = interval

    def _futures_daily(self, day: date) -> tuple[list[FlowBar], DataFileRecord]:
        filename = f"{self.symbol}-{self.interval}-{day.isoformat()}.zip"
        url = f"{BASE_URL}/daily/klines/{self.symbol}/{self.interval}/{filename}"
        path = self.root / self.symbol / self.interval / "daily" / filename
        if not path.exists():
            _download(url, path)
        bars = parse_kline_archive(path)
        return bars, self._record("futures_klines", url, path, len(bars), bars[0].ts_ns, bars[-1].ts_ns)

    def _index_daily(self, day: date) -> tuple[list[IndexBar], DataFileRecord]:
        filename = f"{self.symbol}-{self.interval}-{day.isoformat()}.zip"
        url = f"{BASE_URL}/daily/indexPriceKlines/{self.symbol}/{self.interval}/{filename}"
        path = self.root / self.symbol / "indexPriceKlines" / self.interval / "daily" / filename
        if not path.exists():
            _download(url, path)
        bars = parse_index_archive(path)
        return bars, self._record("index_price_klines", url, path, len(bars), bars[0].ts_ns, bars[-1].ts_ns)

    def _metric_daily(self, day: date) -> tuple[list[MetricSnapshot], DataFileRecord]:
        filename = f"{self.symbol}-metrics-{day.isoformat()}.zip"
        url = f"{BASE_URL}/daily/metrics/{self.symbol}/{filename}"
        path = self.root / self.symbol / "metrics" / "daily" / filename
        if not path.exists():
            _download(url, path)
        metrics = parse_metric_archive(path, expected_symbol=self.symbol)
        return metrics, self._record(
            "metrics", url, path, len(metrics), metrics[0].available_ns, metrics[-1].available_ns
        )

    def daily(self, day: date) -> tuple[list[FlowBar], tuple[DataFileRecord, ...]]:
        bars, futures_record = self._futures_daily(day)
        index_bars, index_record = self._index_daily(day)
        metrics, metric_record = self._metric_daily(day)
        return enrich_bars(bars, metrics, index_bars), (futures_record, index_record, metric_record)

    def _futures_monthly(self, year: int, month: int) -> tuple[list[FlowBar], DataFileRecord]:
        label = f"{year:04d}-{month:02d}"
        filename = f"{self.symbol}-{self.interval}-{label}.zip"
        url = f"{BASE_URL}/monthly/klines/{self.symbol}/{self.interval}/{filename}"
        path = self.root / self.symbol / self.interval / "monthly" / filename
        if not path.exists():
            _download(url, path)
        bars = parse_kline_archive(path)
        return bars, self._record("futures_klines", url, path, len(bars), bars[0].ts_ns, bars[-1].ts_ns)

    def _index_monthly(self, year: int, month: int) -> tuple[list[IndexBar], DataFileRecord]:
        label = f"{year:04d}-{month:02d}"
        filename = f"{self.symbol}-{self.interval}-{label}.zip"
        url = f"{BASE_URL}/monthly/indexPriceKlines/{self.symbol}/{self.interval}/{filename}"
        path = self.root / self.symbol / "indexPriceKlines" / self.interval / "monthly" / filename
        if not path.exists():
            _download(url, path)
        bars = parse_index_archive(path)
        return bars, self._record("index_price_klines", url, path, len(bars), bars[0].ts_ns, bars[-1].ts_ns)

    def _metric_monthly(self, year: int, month: int) -> tuple[list[MetricSnapshot], DataFileRecord]:
        label = f"{year:04d}-{month:02d}"
        filename = f"{self.symbol}-metrics-{label}.zip"
        url = f"{BASE_URL}/monthly/metrics/{self.symbol}/{filename}"
        path = self.root / self.symbol / "metrics" / "monthly" / filename
        if not path.exists():
            _download(url, path)
        metrics = parse_metric_archive(path, expected_symbol=self.symbol)
        return metrics, self._record(
            "metrics", url, path, len(metrics), metrics[0].available_ns, metrics[-1].available_ns
        )

    def monthly(self, year: int, month: int) -> tuple[list[FlowBar], tuple[DataFileRecord, ...]]:
        bars, futures_record = self._futures_monthly(year, month)
        index_bars, index_record = self._index_monthly(year, month)
        metrics, metric_record = self._metric_monthly(year, month)
        return enrich_bars(bars, metrics, index_bars), (futures_record, index_record, metric_record)

    @staticmethod
    def _record(
        data_type: str,
        url: str,
        path: Path,
        rows: int,
        first_observed_ns: int,
        last_observed_ns: int,
    ) -> DataFileRecord:
        return DataFileRecord(
            data_type=data_type,
            source_url=url,
            local_path=str(path),
            sha256=_sha256(path),
            bytes=path.stat().st_size,
            rows=rows,
            first_observed_ns=first_observed_ns,
            last_observed_ns=last_observed_ns,
        )


def validate_coverage(bars: Iterable[FlowBar]) -> CoverageReport:
    ordered = list(bars)
    if not ordered:
        raise ValueError("cannot validate empty data")
    duplicates = 0
    missing = 0
    non_positive = 0
    bars_without_metric = 0
    bars_without_index = 0
    metric_updates = 0
    maximum_age: int | None = None
    prior_ts = -1
    prior_metric: int | None = None
    for bar in ordered:
        if min(bar.open, bar.high, bar.low, bar.close) <= 0.0:
            non_positive += 1
        if bar.ts_ns == prior_ts:
            duplicates += 1
        elif prior_ts >= 0 and bar.ts_ns > prior_ts + ONE_MINUTE_NS:
            missing += max(0, (bar.ts_ns - prior_ts) // ONE_MINUTE_NS - 1)
        elif prior_ts >= 0 and bar.ts_ns < prior_ts:
            raise ValueError("data timestamps are not sorted")
        prior_ts = bar.ts_ns
        if not bar.has_index:
            bars_without_index += 1
        if bar.metric_observed_ns is None:
            bars_without_metric += 1
        else:
            if prior_metric != bar.metric_observed_ns:
                metric_updates += 1
                prior_metric = bar.metric_observed_ns
            age = bar.ts_ns - bar.metric_observed_ns
            maximum_age = age if maximum_age is None else max(maximum_age, age)
    return CoverageReport(
        bars=len(ordered),
        first_observed_ns=ordered[0].ts_ns,
        last_observed_ns=ordered[-1].ts_ns,
        duplicate_timestamps=duplicates,
        missing_minutes=missing,
        non_positive_prices=non_positive,
        metric_updates=metric_updates,
        bars_without_metric=bars_without_metric,
        maximum_metric_age_minutes=(maximum_age / ONE_MINUTE_NS if maximum_age is not None else None),
        bars_without_index=bars_without_index,
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
            bars, records = cache.daily(start + timedelta(days=offset))
            week_bars.extend(bars)
            files.extend(records)
        week_bars.sort(key=lambda bar: bar.ts_ns)
        coverage = validate_coverage(week_bars)
        if coverage.duplicate_timestamps or coverage.non_positive_prices or coverage.bars_without_index:
            raise ValueError(f"invalid futures/index coverage for {name}: {coverage}")
        if coverage.metric_updates < days * 250:
            raise ValueError(f"insufficient five-minute metric coverage for {name}: {coverage}")
        weeks[name] = week_bars
        coverages[name] = coverage
    manifest = {
        "source": "Binance Vision USD-M futures and index-price one-minute klines plus five-minute metrics",
        "symbol": cache.symbol,
        "interval": cache.interval,
        "causality": "same completed one-minute futures/index bars; metrics create_time plus one completed minute",
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
        bars, month_records = cache.monthly(year, month)
        records.extend(month_records)
        all_bars.extend(bar for bar in bars if start_ns < bar.ts_ns <= end_ns)
    all_bars.sort(key=lambda bar: bar.ts_ns)
    coverage = validate_coverage(all_bars)
    if coverage.duplicate_timestamps or coverage.non_positive_prices or coverage.bars_without_index:
        raise ValueError(f"invalid long futures/index coverage: {coverage}")
    manifest = {
        "source": "Binance Vision USD-M futures/index monthly one-minute klines plus five-minute metrics",
        "symbol": cache.symbol,
        "interval": cache.interval,
        "causality": "same completed one-minute futures/index bars; metrics create_time plus one completed minute",
        "start": start.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "files": [asdict(record) for record in records],
        "coverage": asdict(coverage),
    }
    return all_bars, manifest


def write_manifest(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
