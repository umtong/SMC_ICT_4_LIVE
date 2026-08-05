#!/usr/bin/env python3
"""Evaluate an arbitrary BTC interval with the frozen daily-clock candidate.

This is the staged long-evaluation runner for
``intrinsic_external_liquidity_v2_daily_week``.  It changes no scenario or
execution rule.  The interval is explicit, while four completed context days
and one additional causal clock-source day are loaded before the evaluation.

The intended research sequence is one calendar quarter first.  A structurally
failed quarter stops the expensive year run; a viable quarter is followed by
the remaining fixed calendar quarters and a deterministic aggregate.  Quarter
boundaries are not strategy parameters.
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
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan, simulate  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import (  # noqa: E402
    CLOCK_SOURCE_EXTRA_DAYS,
    DAILY_CANDIDATE_MINUTES,
    ROUND_TRIP_COST_BPS,
)
from intrinsic_external_liquidity_v2_week import (  # noqa: E402
    CONTEXT_WARMUP_DAYS,
    LONG_RANGE_HOURS,
    LONG_RANGE_OUTER_FRACTION,
    NS_PER_HOUR,
    OUTER_DIRECTIONAL_CHANGE_FRACTION,
    OUTER_DIRECTIONAL_CHANGE_MULTIPLE,
    SHORT_RANGE_HOURS,
    STRONG_AGAINST_DELIVERY_FRACTION,
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
    evaluation_start = parse_utc_date(args.start)
    evaluation_end = parse_utc_date(args.end)
    if evaluation_end <= evaluation_start:
        raise ValueError("--end must be after --start")
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
    plans: list[ScenarioPlan] = []
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

    trades, metrics, daily, rejections = simulate(
        features=features,
        plans=plans,
        evaluation_start_ns=start_ns,
        evaluation_end_ns=end_ns,
        starting_nav=float(execution["starting_nav"]),
        cost=cost,
        exit_on_boundary_reacceptance=False,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    decision_counts = Counter(row.reason_code for row in decisions)
    evaluation_bars = [
        row.bar for row in features if start_ns <= row.bar.end_time_ns < end_ns
    ]
    start_day = evaluation_start.date().isoformat()
    end_day = evaluation_end.date().isoformat()
    evaluation_calibrations = [
        item for item in calibrations if start_day <= item.bar_day < end_day
    ]

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_nav.csv", index=False)
    rejections.to_csv(output / "rejections.csv", index=False)
    pd.DataFrame(asdict(row) for row in evaluation_signals).to_csv(
        output / "retest_signals.csv",
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
        "candidate": "target-free intrinsic sweep with daily causal external-liquidity routing",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "evaluation_days": (evaluation_end - evaluation_start).total_seconds() / 86_400.0,
        "context_start_utc": context_start.isoformat(),
        "clock_source_start_utc": clock_source_start.isoformat(),
        "daily_candidate_minutes": list(DAILY_CANDIDATE_MINUTES),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "evaluation_selected_minutes": [
            item.selected_minutes for item in evaluation_calibrations
        ],
        "evaluation_target_quote_notional": [
            item.selected_target_quote_notional
            for item in evaluation_calibrations
        ],
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": (
            len(evaluation_bars)
            / ((evaluation_end - evaluation_start).total_seconds() / 86_400.0)
        ),
        "evaluation_retest_signals": len(evaluation_signals),
        "routed_plans": len(plans),
        "decision_counts": dict(decision_counts),
        "detector_counts": dict(detector.counts),
        "router": {
            "short_range_hours": SHORT_RANGE_HOURS,
            "long_range_hours": LONG_RANGE_HOURS,
            "long_range_outer_fraction": LONG_RANGE_OUTER_FRACTION,
            "strong_against_delivery_fraction": STRONG_AGAINST_DELIVERY_FRACTION,
            "outer_directional_change_multiple": OUTER_DIRECTIONAL_CHANGE_MULTIPLE,
            "outer_directional_change_fraction": OUTER_DIRECTIONAL_CHANGE_FRACTION,
            "target_policy": "nearest unconsumed confirmed opposing pivot clearing signal-close cost and net RR",
        },
        "risk_fraction": 0.03,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": True,
        "segment_boundary_policy": "force close at explicit evaluation end; reconstruct four completed context days at next fixed segment",
    }
    atomic_json(
        output / "intrinsic_external_liquidity_v2_daily_period_summary.json",
        payload,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-intrinsic-external-v2-period",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-intrinsic-external-v2-period",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
