#!/usr/bin/env python3
"""Audit frequency and completeness of official Binance Vision archives.

Downloads only a frozen bounded set of small daily BTCUSDT `bookDepth` and
`metrics` archives plus one bounded `bookTicker` day. It records row counts,
coverage, timestamp spacing, schema and duplicate/order anomalies. This is data
infrastructure evidence only, never a performance engine.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from statistics import median
import tempfile
import urllib.request
import zipfile

BASE = "https://data.binance.vision/data/futures/um/daily"
USER_AGENT = "SMC-ICT-4-LIVE-candidate-01-frequency-audit/1.0"
BOOK_DEPTH_DATES = (
    "2023-01-02",
    "2024-03-01",
    "2025-05-12",
    "2026-08-05",
)
METRICS_DATES = BOOK_DEPTH_DATES
BOOK_TICKER_DATES = ("2023-09-09",)


@dataclass(frozen=True, slots=True)
class Audit:
    dataset: str
    date: str
    url: str
    compressed_bytes: int
    member: str
    columns: tuple[str, ...]
    rows: int
    first_timestamp: str | None
    last_timestamp: str | None
    unique_timestamps: int
    duplicate_timestamp_rows: int
    timestamp_regressions: int
    minimum_interval_seconds: float | None
    median_interval_seconds: float | None
    maximum_interval_seconds: float | None
    expected_timestamp_count: int | None
    coverage_fraction: float | None
    rows_per_timestamp_minimum: int | None
    rows_per_timestamp_median: float | None
    rows_per_timestamp_maximum: int | None
    percentage_values: tuple[int, ...] | None
    numeric_parse_failures: int


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        with destination.open("wb") as output:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                output.write(block)


def parse_time(dataset: str, value: str) -> int:
    if dataset == "bookTicker":
        return int(value)
    dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc,
    )
    return int(dt.timestamp() * 1000)


def expected_count(dataset: str) -> int | None:
    if dataset == "bookDepth":
        return 1440
    if dataset == "metrics":
        return 288
    return None


def archive_url(dataset: str, date: str) -> str:
    return (
        f"{BASE}/{dataset}/BTCUSDT/"
        f"BTCUSDT-{dataset}-{date}.zip"
    )


def timestamp_column(dataset: str) -> str:
    return {
        "bookDepth": "timestamp",
        "metrics": "create_time",
        "bookTicker": "event_time",
    }[dataset]


def audit(dataset: str, date: str, archive: Path) -> Audit:
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        if len(names) != 1:
            raise RuntimeError(f"expected one CSV member, found {names}")
        member = names[0]
        with zipped.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.DictReader(text)
            if reader.fieldnames is None:
                raise RuntimeError("missing CSV header")
            columns = tuple(reader.fieldnames)
            time_col = timestamp_column(dataset)
            if time_col not in columns:
                raise RuntimeError(f"missing timestamp column {time_col}")

            rows = 0
            regressions = 0
            numeric_failures = 0
            prior_ms: int | None = None
            counts: dict[int, int] = {}
            percentages: set[int] = set()
            first_raw: str | None = None
            last_raw: str | None = None
            for row in reader:
                rows += 1
                raw_time = row[time_col]
                try:
                    ts_ms = parse_time(dataset, raw_time)
                except (TypeError, ValueError):
                    numeric_failures += 1
                    continue
                if first_raw is None:
                    first_raw = raw_time
                last_raw = raw_time
                if prior_ms is not None and ts_ms < prior_ms:
                    regressions += 1
                prior_ms = ts_ms
                counts[ts_ms] = counts.get(ts_ms, 0) + 1
                if dataset == "bookDepth":
                    try:
                        percentages.add(int(row["percentage"]))
                        float(row["depth"])
                        float(row["notional"])
                    except (KeyError, TypeError, ValueError):
                        numeric_failures += 1
                elif dataset == "metrics":
                    for field in (
                        "sum_open_interest",
                        "sum_open_interest_value",
                        "sum_taker_long_short_vol_ratio",
                    ):
                        try:
                            float(row[field])
                        except (KeyError, TypeError, ValueError):
                            numeric_failures += 1
                else:
                    for field in (
                        "best_bid_price",
                        "best_bid_qty",
                        "best_ask_price",
                        "best_ask_qty",
                    ):
                        try:
                            float(row[field])
                        except (KeyError, TypeError, ValueError):
                            numeric_failures += 1

    ordered = sorted(counts)
    intervals = [
        (right - left) / 1000.0
        for left, right in zip(ordered, ordered[1:], strict=False)
        if right > left
    ]
    per_timestamp = list(counts.values())
    expected = expected_count(dataset)
    coverage = len(counts) / expected if expected else None
    return Audit(
        dataset=dataset,
        date=date,
        url=archive_url(dataset, date),
        compressed_bytes=archive.stat().st_size,
        member=member,
        columns=columns,
        rows=rows,
        first_timestamp=first_raw,
        last_timestamp=last_raw,
        unique_timestamps=len(counts),
        duplicate_timestamp_rows=rows - len(counts),
        timestamp_regressions=regressions,
        minimum_interval_seconds=min(intervals) if intervals else None,
        median_interval_seconds=median(intervals) if intervals else None,
        maximum_interval_seconds=max(intervals) if intervals else None,
        expected_timestamp_count=expected,
        coverage_fraction=coverage,
        rows_per_timestamp_minimum=min(per_timestamp) if per_timestamp else None,
        rows_per_timestamp_median=(
            median(per_timestamp) if per_timestamp else None
        ),
        rows_per_timestamp_maximum=max(per_timestamp) if per_timestamp else None,
        percentage_values=(tuple(sorted(percentages)) if percentages else None),
        numeric_parse_failures=numeric_failures,
    )


def run(args: argparse.Namespace) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    audits: list[Audit] = []
    failures: list[dict[str, str]] = []
    tasks = [
        *(('bookDepth', date) for date in BOOK_DEPTH_DATES),
        *(('metrics', date) for date in METRICS_DATES),
        *(('bookTicker', date) for date in BOOK_TICKER_DATES),
    ]
    with tempfile.TemporaryDirectory(prefix="binance-vision-frequency-") as tmp:
        temp = Path(tmp)
        for dataset, date in tasks:
            url = archive_url(dataset, date)
            path = temp / f"{dataset}-{date}.zip"
            try:
                download(url, path)
                audits.append(audit(dataset, date, path))
            except Exception as exc:  # noqa: BLE001 - evidence includes failures
                failures.append(
                    {
                        "dataset": dataset,
                        "date": date,
                        "url": url,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )

    payload = {
        "audit": "official Binance Vision BTCUSDT archive frequency",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "performance_research": False,
        "custom_market_data_source": False,
        "frozen_tasks": [
            {"dataset": dataset, "date": date}
            for dataset, date in tasks
        ],
        "results": [asdict(row) for row in audits],
        "failures": failures,
    }
    (args.output / "microstructure_frequency_audit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if failures:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-01-microstructure-frequency-audit"),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
