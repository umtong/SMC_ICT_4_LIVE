#!/usr/bin/env python3
"""Export causal equal-notional event features for one BTC research week.

This diagnostic uses the exact data, prior-day clock calibration and completed
bar construction of ``directional_change_failed_sweep_week.py``.  It contains
no future labels and performs no strategy optimization.  The resulting event
stream is sufficient to replay scenario state machines and execution controls
locally without repeatedly downloading aggregate trades.
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

from aggtrade_clock import calibrate_target_from_minutes, iter_volume_bars, minute_quote_totals  # noqa: E402
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    FailedSweepRetestStateMachine,
)
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
    warmup_ns = int(pd.Timestamp(warmup).as_unit("ns").value)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup,
        end=end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    minute_totals = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    target_quote = calibrate_target_from_minutes(
        minute_totals,
        minutes_per_event=CLOCK_CALIBRATION_MINUTES,
    )
    bars = list(
        iter_volume_bars(
            iter_downloads(records),
            target_quote_notional=target_quote,
            include_partial=False,
        ),
    )

    detector = ImpactRegimeDetector()
    scenario = FailedSweepRetestStateMachine()
    rows: list[dict[str, Any]] = []
    for bar in bars:
        detector.on_bar(bar)
        index = len(detector.features) - 1
        feature = detector.features[-1]
        scenario.on_feature(index=index, features=detector.features)
        rows.append(
            {
                **asdict(bar),
                "true_range": feature.true_range,
                "atr": feature.atr,
                "imbalance_z": feature.imbalance_z,
                "raw_imbalance": bar.imbalance,
                "return_fraction": bar.return_fraction,
                "range_fraction": bar.range_fraction,
                "close_location": bar.close_location,
                "duration_seconds": bar.duration_seconds,
                "in_evaluation": start_ns <= bar.end_time_ns < end_ns,
            },
        )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output / "event_features.csv", index=False)
    pd.DataFrame(asdict(row) for row in scenario.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.plans).to_csv(
        output / "scenario_plans.csv",
        index=False,
    )
    payload = {
        "symbol": "BTCUSDT",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "warmup_start_utc": warmup.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "event_rows": len(rows),
        "evaluation_event_rows": sum(
            1 for row in rows if bool(row["in_evaluation"])
        ),
        "directional_change_events": len(scenario.detector.events),
        "scenario_plans": len(scenario.plans),
        "scenario_counts": dict(scenario.counts),
        "downloads": [record.to_dict() for record in records],
        "contains_future_labels": False,
        "contains_strategy_pnl": False,
    }
    atomic_json(output / "directional_change_feature_manifest.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-dc-feature-export")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-dc-feature-export")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
