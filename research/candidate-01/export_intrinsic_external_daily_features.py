#!/usr/bin/env python3
"""Export one frozen daily-clock BTC week for scenario-supersession diagnosis.

The export reproduces the target-free intrinsic detector and causal 24h/72h
router exactly.  It writes completed event features, emitted retest signals,
routed plans and routing decisions but does not alter or optimize execution.
The primary use is to determine whether a later same-side scenario should
causally supersede an already active earlier scenario.
"""

from __future__ import annotations

import argparse
from collections import Counter
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

from adaptive_aggtrade_clock import build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
from data import parse_utc_date  # noqa: E402
from impact_regime_probe import ImpactRegimeDetector  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import (  # noqa: E402
    CLOCK_SOURCE_EXTRA_DAYS,
    DAILY_CANDIDATE_MINUTES,
    ROUND_TRIP_COST_BPS,
)
from intrinsic_external_liquidity_v2_week import (  # noqa: E402
    CONTEXT_WARMUP_DAYS,
    RoutingDecision,
    TargetFreeSweepRetestDetector,
    outer_state_series,
    route_signal,
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
    context_start = evaluation_start - timedelta(days=CONTEXT_WARMUP_DAYS)
    clock_source_start = context_start - timedelta(days=CLOCK_SOURCE_EXTRA_DAYS)
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
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=DAILY_CANDIDATE_MINUTES,
    )

    feature_detector = ImpactRegimeDetector()
    detector = TargetFreeSweepRetestDetector()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        detector.on_feature(index=index, features=feature_detector.features)

    features = feature_detector.features
    end_times = [row.bar.end_time_ns for row in features]
    outer_states = outer_state_series(features)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    minimum_price_fraction = float(execution["minimum_price_risk_fraction"])
    minimum_net_rr = float(execution["minimum_net_reward_risk"])
    evaluation_signals = [
        signal
        for signal in detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    plans = []
    decisions: list[RoutingDecision] = []
    for signal in evaluation_signals:
        plan, decision = route_signal(
            signal=signal,
            features=features,
            end_times=end_times,
            outer_states=outer_states,
            events=detector.detector.events,
            cost=cost,
            minimum_price_risk_fraction=minimum_price_fraction,
            minimum_net_reward_risk=minimum_net_rr,
        )
        decisions.append(decision)
        if plan is not None:
            plans.append(plan)

    feature_rows: list[dict[str, Any]] = []
    for index, row in enumerate(features):
        feature_rows.append(
            {
                **asdict(row.bar),
                "feature_index": index,
                "true_range": row.true_range,
                "atr": row.atr,
                "imbalance_z": row.imbalance_z,
                "raw_imbalance": row.bar.imbalance,
                "return_fraction": row.bar.return_fraction,
                "range_fraction": row.bar.range_fraction,
                "close_location": row.bar.close_location,
                "duration_seconds": row.bar.duration_seconds,
                "outer_state": outer_states[index],
                "in_evaluation": start_ns <= row.bar.end_time_ns < end_ns,
            },
        )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(feature_rows).to_csv(output / "event_features.csv", index=False)
    pd.DataFrame(asdict(row) for row in evaluation_signals).to_csv(
        output / "retest_signals.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in plans).to_csv(
        output / "routed_plans.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in decisions).to_csv(
        output / "routing_decisions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in detector.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json",
        orient="records",
        indent=2,
    )
    payload = {
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_start_utc": context_start.isoformat(),
        "clock_source_start_utc": clock_source_start.isoformat(),
        "event_features": len(feature_rows),
        "evaluation_event_features": sum(
            1 for row in feature_rows if bool(row["in_evaluation"])
        ),
        "evaluation_retest_signals": len(evaluation_signals),
        "routed_plans": len(plans),
        "decision_counts": dict(Counter(row.reason_code for row in decisions)),
        "contains_future_labels": False,
        "contains_strategy_pnl": False,
        "downloads": [record.to_dict() for record in records],
    }
    atomic_json(output / "feature_export_manifest.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-intrinsic-external-daily-export",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-intrinsic-external-daily-export",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
