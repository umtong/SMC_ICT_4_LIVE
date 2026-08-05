#!/usr/bin/env python3
"""Verify and profile official BTCUSDT aggregate trades for the first week."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_clock import (  # noqa: E402
    calibrate_target_from_minutes,
    iter_volume_bars,
    minute_quote_totals,
)
from aggtrade_data import (  # noqa: E402
    download_aggtrade_days,
    inspect_download,
    iter_downloads,
)
from data import parse_utc_date  # noqa: E402


CLOCK_MINUTES = (1, 2, 5)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {key: None for key in ("min", "p10", "p25", "p50", "p75", "p90", "p99", "max")}
    series = pd.Series(values, dtype=float)
    return {
        "min": float(series.min()),
        "p10": float(series.quantile(0.10)),
        "p25": float(series.quantile(0.25)),
        "p50": float(series.quantile(0.50)),
        "p75": float(series.quantile(0.75)),
        "p90": float(series.quantile(0.90)),
        "p99": float(series.quantile(0.99)),
        "max": float(series.max()),
    }


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    research = dict(raw["research"])
    evaluation_start = parse_utc_date(str(research["discovery_week"]))
    evaluation_end = evaluation_start + timedelta(days=7)
    warmup_start = evaluation_start - timedelta(days=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    warmup_ns = int(pd.Timestamp(warmup_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    with ThreadPoolExecutor(max_workers=min(args.workers, len(records))) as executor:
        file_stats = list(executor.map(inspect_download, records))

    minute_totals = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    targets = {
        minutes: calibrate_target_from_minutes(
            minute_totals,
            minutes_per_event=minutes,
        )
        for minutes in CLOCK_MINUTES
    }

    clocks: dict[str, Any] = {}
    bar_csv_rows: list[dict[str, object]] = []
    for minutes, target in targets.items():
        bars = list(
            iter_volume_bars(
                iter_downloads(records),
                target_quote_notional=target,
                include_partial=False,
            ),
        )
        evaluation_bars = [
            bar for bar in bars if start_ns <= bar.end_time_ns < end_ns
        ]
        by_day: dict[str, int] = {}
        for bar in evaluation_bars:
            day = pd.Timestamp(bar.end_time_ns, unit="ns", tz="UTC").date().isoformat()
            by_day[day] = by_day.get(day, 0) + 1
        duration = [bar.duration_seconds for bar in evaluation_bars]
        trades_per_bar = [float(bar.aggregate_trades) for bar in evaluation_bars]
        imbalance = [bar.imbalance for bar in evaluation_bars]
        range_bps = [bar.range_fraction * 10_000.0 for bar in evaluation_bars]
        return_bps = [bar.return_fraction * 10_000.0 for bar in evaluation_bars]
        clocks[f"{minutes}m-median-notional"] = {
            "minutes_per_calibration_bucket": minutes,
            "target_quote_notional": target,
            "warmup_buckets": len(minute_totals),
            "evaluation_bars": len(evaluation_bars),
            "bars_per_day": len(evaluation_bars) / 7.0,
            "bars_by_day": by_day,
            "duration_seconds": quantiles(duration),
            "aggregate_trades_per_bar": quantiles(trades_per_bar),
            "imbalance": quantiles(imbalance),
            "range_bps": quantiles(range_bps),
            "return_bps": quantiles(return_bps),
        }
        if minutes == 1:
            bar_csv_rows = [bar.to_dict() for bar in evaluation_bars]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(bar_csv_rows).to_csv(output / "one_minute_clock_bars.csv", index=False)
    payload = {
        "dataset": "official Binance USD-M futures BTCUSDT aggTrades",
        "warmup_start_utc": warmup_start.isoformat(),
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "downloads": [record.to_dict() for record in records],
        "compressed_size_bytes": sum(record.size_bytes for record in records),
        "file_stats": [item.to_dict() for item in sorted(file_stats, key=lambda row: row.day)],
        "warmup_minute_quote_notional": quantiles(list(minute_totals.values())),
        "clocks": clocks,
        "integrity": {
            "all_checksums_match": all(record.sha256 == record.expected_sha256 for record in records),
            "all_files_nonempty": all(item.rows > 0 for item in file_stats),
            "timestamp_regressions": sum(item.non_monotonic_timestamps for item in file_stats),
            "id_regressions": sum(item.non_monotonic_ids for item in file_stats),
            "duplicate_ids": sum(item.duplicate_agg_trade_ids for item in file_stats),
        },
    }
    atomic_json(output / "aggtrade_doctor.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-aggtrades",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-aggtrade-doctor",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
