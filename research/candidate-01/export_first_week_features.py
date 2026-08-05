#!/usr/bin/env python3
"""Export the frozen first BTC week's causal research features once.

This is a data diagnostic, not a strategy or backtest engine.  It downloads the
same checksum-verified aggregate-trade files, builds completed UTC-aligned 5m
structure bars and 1m execution bars, and writes every field needed for rapid
local scenario diagnosis.  One warmup day is retained and explicitly marked;
no future-derived labels or PnL fields are included.
"""

from __future__ import annotations

import argparse
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

from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from aggtrade_time_clock import iter_time_bars  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import ImpactRegimeDetector  # noqa: E402


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    start = parse_utc_date(args.week)
    end = start + timedelta(days=7)
    warmup = start - timedelta(days=1)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(end).as_unit("ns").value)
    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup,
        end=end,
        cache_dir=args.cache,
        workers=args.workers,
    )

    five_bars = list(
        iter_time_bars(
            iter_downloads(records),
            interval_minutes=5,
            include_partial=False,
        ),
    )
    detector = ImpactRegimeDetector()
    five_rows: list[dict[str, Any]] = []
    for bar in five_bars:
        detector.on_bar(bar)
        feature = detector.features[-1]
        five_rows.append(
            {
                **asdict(bar),
                "true_range": feature.true_range,
                "atr": feature.atr,
                "imbalance_z": feature.imbalance_z,
                "raw_imbalance": bar.imbalance,
                "range_fraction": bar.range_fraction,
                "duration_seconds": bar.duration_seconds,
                "in_evaluation": start_ns <= bar.end_time_ns < end_ns,
            },
        )

    one_bars = list(
        iter_time_bars(
            iter_downloads(records),
            interval_minutes=1,
            include_partial=False,
        ),
    )
    one_rows = [
        {
            **asdict(bar),
            "raw_imbalance": bar.imbalance,
            "range_fraction": bar.range_fraction,
            "duration_seconds": bar.duration_seconds,
            "in_evaluation": start_ns <= bar.end_time_ns < end_ns,
        }
        for bar in one_bars
    ]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(five_rows).to_csv(output / "five_minute_features.csv", index=False)
    pd.DataFrame(one_rows).to_csv(output / "one_minute_bars.csv", index=False)
    pd.DataFrame(asdict(row) for row in detector.pulse_events).to_csv(
        output / "pulse_events.csv",
        index=False,
    )
    payload = {
        "symbol": "BTCUSDT",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "warmup_start_utc": warmup.isoformat(),
        "five_minute_rows": len(five_rows),
        "one_minute_rows": len(one_rows),
        "pulse_events": len(detector.pulse_events),
        "downloads": [record.to_dict() for record in records],
        "contains_future_labels": False,
        "contains_strategy_pnl": False,
    }
    atomic_json(output / "feature_export_manifest.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", default="2023-06-19")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-timebar-aggtrades")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-first-week-features")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
