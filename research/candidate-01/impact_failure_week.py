#!/usr/bin/env python3
"""Run the frozen strict impact-failure candidate on one named BTC week.

This runner exists solely for sequential short gates.  It does not search or
modify parameters.  For each requested week it calibrates the equal-notional
clock from the immediately preceding UTC day, generates strict online
failed-impact plans, and executes them with the frozen baseline policy:

* three-event causal failure-confirmation window;
* opposite flow z >= 0.50;
* close at least 0.05 event ATR back inside the broken boundary and through the
  initiative pulse midpoint;
* stop beyond every observed confirmation-path extreme plus 0.15 ATR;
* target the opposite edge of the pre-impact structure;
* next-event open, confirmation hold, stop-first ambiguity;
* 7 bps per side, 3% current-NAV risk, one global position;
* no optional boundary-reacceptance overlay.

One invocation evaluates exactly seven days and cannot run a long period.
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

from aggtrade_clock import (  # noqa: E402
    calibrate_target_from_minutes,
    iter_volume_bars,
    minute_quote_totals,
)
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_failure_candidate import (  # noqa: E402
    CONFIRMATION_WINDOW_BARS,
    INSIDE_DEPTH_ATR,
    OPPOSITE_FLOW_CONFIRM_Z,
    RISK_RATE,
    STOP_BUFFER_ATR,
    ImpactFailureStateMachine,
)
from impact_regime_probe import (  # noqa: E402
    CLOCK_CALIBRATION_MINUTES,
    ImpactRegimeDetector,
    simulate,
)


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
    target_quote = calibrate_target_from_minutes(
        warmup_minutes,
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

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in candidate.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    payload = {
        "candidate": "frozen strict causal impact-failure reversal",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "warmup_start_utc": warmup_start.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
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
    atomic_json(output / "impact_failure_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True, help="UTC Monday/date in YYYY-MM-DD")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-impact-failure-week",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-impact-failure-week",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
