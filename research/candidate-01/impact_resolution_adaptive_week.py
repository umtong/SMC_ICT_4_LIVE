#!/usr/bin/env python3
"""Run resolved-impact with a causal daily cost-resolvable information clock.

The resolved-impact scenario, three-event outcome ordering, confirmation,
strict path stop, targets, execution, costs and 3% NAV risk are unchanged.
Only the event clock is normalized each UTC day from the immediately preceding
completed day.

The minimum median event range is not optimized from PnL.  The execution gate
requires price risk to be at least 65% of planned loss.  With 14 bps round-trip
cost, the price move needed to satisfy that identity is:

    14 bps * 0.65 / (1 - 0.65) = 26 bps.

Among 5, 10, 20 and 30 minute-equivalent equal-notional candidates, the
smallest preceding-day clock with median high-low range at least 26 bps is
frozen for the next day.  One invocation evaluates exactly one BTC week.
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

from adaptive_aggtrade_clock import DEFAULT_CANDIDATE_MINUTES, build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import MINIMUM_PRICE_RISK_FRACTION, ImpactRegimeDetector, simulate  # noqa: E402
from impact_resolution_candidate import (  # noqa: E402
    CONTINUATION_FLOW_SUM_Z,
    INSIDE_DEPTH_ATR,
    MIN_OUTSIDE_HOLDS,
    OPPOSITE_FLOW_CONFIRM_Z,
    OUTSIDE_DEPTH_ATR,
    RESOLUTION_WINDOW_BARS,
    STOP_BUFFER_ATR,
    ImpactResolutionStateMachine,
)


ROUND_TRIP_COST_BPS = 14.0
MINIMUM_EVENT_RANGE_BPS = (
    ROUND_TRIP_COST_BPS
    * MINIMUM_PRICE_RISK_FRACTION
    / (1.0 - MINIMUM_PRICE_RISK_FRACTION)
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
        minimum_range_bps=MINIMUM_EVENT_RANGE_BPS,
        candidate_minutes=DEFAULT_CANDIDATE_MINUTES,
    )

    detector = ImpactRegimeDetector()
    resolver = ImpactResolutionStateMachine()
    previous_initiatives = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        initiatives = detector.continuation_plans[previous_initiatives:]
        previous_initiatives = len(detector.continuation_plans)
        resolver.on_feature(
            index=index,
            feature=detector.features[-1],
            new_initiative_plans=initiatives,
        )

    trades, metrics, daily, rejections = simulate(
        features=detector.features,
        plans=resolver.plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        exit_on_boundary_reacceptance=False,
    )
    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]
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
    pd.DataFrame(asdict(row) for row in resolver.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json",
        orient="records",
        indent=2,
    )
    payload = {
        "candidate": "resolved impact with causal daily cost-resolvable clock",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "feature_warmup_start_utc": feature_warmup_start.isoformat(),
        "clock_source_start_utc": clock_source_start.isoformat(),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "minimum_price_risk_fraction": MINIMUM_PRICE_RISK_FRACTION,
        "minimum_event_range_bps": MINIMUM_EVENT_RANGE_BPS,
        "candidate_clock_minutes": list(DEFAULT_CANDIDATE_MINUTES),
        "daily_clock_calibrations": [item.to_dict() for item in calibrations],
        "evaluation_selected_minutes": [item.selected_minutes for item in evaluation_calibrations],
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "resolution_window_bars": RESOLUTION_WINDOW_BARS,
        "opposite_flow_confirm_z": OPPOSITE_FLOW_CONFIRM_Z,
        "continuation_flow_sum_z": CONTINUATION_FLOW_SUM_Z,
        "inside_depth_atr": INSIDE_DEPTH_ATR,
        "outside_depth_atr": OUTSIDE_DEPTH_ATR,
        "minimum_outside_holds": MIN_OUTSIDE_HOLDS,
        "stop_buffer_atr": STOP_BUFFER_ATR,
        "risk_fraction": 0.03,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "detector_counts": dict(detector.counts),
        "resolution_counts": dict(resolver.counts),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_resolution_adaptive_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-impact-resolution-adaptive")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-impact-resolution-adaptive")
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
