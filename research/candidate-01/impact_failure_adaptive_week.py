#!/usr/bin/env python3
"""Run the frozen strict impact-failure candidate with a daily causal clock.

The scenario, confirmation, entry, stop, target, cost, risk and global-position
rules are identical to ``impact_failure_week.py``.  The only changed variable
is market-time normalization: each UTC day uses the smallest equal-notional
clock whose immediately preceding day's median event range clears the frozen
14 bps round-trip cost stress.

One invocation evaluates exactly one seven-day BTC interval.
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

from adaptive_aggtrade_clock import (  # noqa: E402
    DEFAULT_CANDIDATE_MINUTES,
    build_daily_cost_resolved_bars,
)
from aggtrade_data import download_aggtrade_days  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_failure_candidate import (  # noqa: E402
    CONFIRMATION_WINDOW_BARS,
    INSIDE_DEPTH_ATR,
    OPPOSITE_FLOW_CONFIRM_Z,
    RISK_RATE,
    STOP_BUFFER_ATR,
    ImpactFailureStateMachine,
)
from impact_regime_probe import ImpactRegimeDetector, simulate  # noqa: E402


ROUND_TRIP_COST_BPS = 14.0


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    feature_warmup_start = evaluation_start - timedelta(days=1)
    clock_source_start = evaluation_start - timedelta(days=2)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=feature_warmup_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=DEFAULT_CANDIDATE_MINUTES,
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
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
    )
    evaluation_bars = [
        bar for bar in bars if start_ns <= bar.end_time_ns < end_ns
    ]
    evaluation_calibrations = [
        item
        for item in calibrations
        if evaluation_start.date().isoformat() <= item.bar_day < evaluation_end.date().isoformat()
    ]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in candidate.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json",
        orient="records",
        indent=2,
    )
    payload = {
        "candidate": "frozen strict impact-failure with daily cost-resolved clock",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "feature_warmup_start_utc": feature_warmup_start.isoformat(),
        "clock_source_start_utc": clock_source_start.isoformat(),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "candidate_clock_minutes": list(DEFAULT_CANDIDATE_MINUTES),
        "daily_clock_calibrations": [item.to_dict() for item in calibrations],
        "evaluation_selected_minutes": [item.selected_minutes for item in evaluation_calibrations],
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "confirmation_window_bars": CONFIRMATION_WINDOW_BARS,
        "opposite_flow_confirm_z": OPPOSITE_FLOW_CONFIRM_Z,
        "inside_depth_atr": INSIDE_DEPTH_ATR,
        "stop_buffer_atr": STOP_BUFFER_ATR,
        "risk_fraction": RISK_RATE,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "detector_counts": dict(detector.counts),
        "candidate_state_counts": dict(candidate.counts),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_failure_adaptive_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-impact-failure-adaptive",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-impact-failure-adaptive",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
