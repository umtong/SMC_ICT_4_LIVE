#!/usr/bin/env python3
"""First-week nested market-time diagnosis for strict impact failure.

The scenario and execution are frozen.  Only the equal-notional market-time
scale changes across a predeclared geometric ladder:

* 10 minutes of prior-day median quote activity: finest scale expected to clear
  the 14 bps round-trip cost;
* 20 minutes: frozen baseline scale;
* 40 minutes: one coarser nested auction scale.

Each scale is calibrated from the completed UTC day before the first BTC week,
then remains fixed for the entire week.  Every scale runs independently first.
This program does not combine scales and does not select the best result.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
from statistics import median
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
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_failure_candidate import ImpactFailureStateMachine  # noqa: E402
from impact_regime_probe import ImpactRegimeDetector, simulate  # noqa: E402


SCALES_MINUTES = (10, 20, 40)
ROUND_TRIP_COST_BPS = 14.0


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_scale(
    *,
    scale_minutes: int,
    target_quote_notional: float,
    records: list[Any],
    start_ns: int,
    end_ns: int,
    starting_nav: float,
    cost: float,
    output: Path,
) -> dict[str, Any]:
    bars = list(
        iter_volume_bars(
            iter_downloads(records),
            target_quote_notional=target_quote_notional,
            include_partial=False,
        ),
    )
    detector = ImpactRegimeDetector()
    candidate = ImpactFailureStateMachine(
        include_intermediate_extremes=True,
        reject_consumed_target=True,
    )
    for index, bar in enumerate(bars):
        detector_plans = detector.on_bar(bar)
        feature = detector.features[-1]
        initiatives = [plan for plan in detector_plans if plan.response == "CONTINUATION"]
        candidate.on_feature(
            index=index,
            feature=feature,
            new_initiative_plans=initiatives,
        )

    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=candidate.plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=starting_nav,
        cost=cost,
        exit_on_boundary_reacceptance=False,
    )
    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]
    ranges = [bar.range_fraction * 10_000.0 for bar in evaluation_bars]
    durations = [bar.duration_seconds for bar in evaluation_bars]

    destination = output / f"{scale_minutes}m"
    destination.mkdir(parents=True, exist_ok=True)
    trades.to_csv(destination / "trades.csv", index=False)
    daily.to_csv(destination / "daily_nav.csv", index=False)
    rejections.to_csv(destination / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in candidate.transitions).to_csv(
        destination / "scenario_transitions.csv",
        index=False,
    )
    atomic_json(destination / "metrics.json", metrics)
    return {
        "scale_minutes": scale_minutes,
        "target_quote_notional": target_quote_notional,
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "median_event_range_bps": float(median(ranges)) if ranges else None,
        "median_event_duration_seconds": float(median(durations)) if durations else None,
        "cost_resolved_in_evaluation": bool(
            ranges and median(ranges) >= ROUND_TRIP_COST_BPS
        ),
        "detector_counts": dict(detector.counts),
        "candidate_state_counts": dict(candidate.counts),
        "metrics": metrics,
    }


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(str(research["discovery_week"]))
    evaluation_end = evaluation_start + timedelta(days=7)
    warmup_start = evaluation_start - timedelta(days=1)
    warmup_ns = int(pd.Timestamp(warmup_start).as_unit("ns").value)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    warmup_minutes = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    targets = {
        scale: calibrate_target_from_minutes(
            warmup_minutes,
            minutes_per_event=scale,
        )
        for scale in SCALES_MINUTES
    }

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    results = {
        str(scale): run_scale(
            scale_minutes=scale,
            target_quote_notional=targets[scale],
            records=records,
            start_ns=start_ns,
            end_ns=end_ns,
            starting_nav=float(execution["starting_nav"]),
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
            output=output,
        )
        for scale in SCALES_MINUTES
    }
    payload = {
        "diagnosis": "strict failed-impact reversal across nested fixed market-time scales",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "warmup_start_utc": warmup_start.isoformat(),
        "predeclared_scales_minutes": list(SCALES_MINUTES),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "risk_fraction": 0.03,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "results": results,
        "downloads": [record.to_dict() for record in records],
        "combined_scale_run": False,
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_failure_scale_summary.json", payload)
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
        default=ROOT / "artifacts" / "candidate-01-impact-failure-scales",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
