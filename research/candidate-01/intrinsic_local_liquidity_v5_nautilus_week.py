#!/usr/bin/env python3
"""First-week NautilusTrader control for local failed-auction rotation.

This is a one-variable control against the hierarchical v4 router.  Detection,
40-bps intrinsic structure, sweep/re-entry, order-flow reversal, boundary
retest, path-extreme invalidation, cost model, delayed execution and 3% current
NAV risk are unchanged.  Only the 24h/72h location gate is removed.

A confirmed local failed auction targets the nearest still-unconsumed opposing
intrinsic pivot which clears the same cost and net reward/risk geometry.  Signal
generation owns no fill, PnL or NAV logic; all performance is produced by the
pinned NautilusTrader engine using official Binance Vision one-minute bars.
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
from data import load_interval, parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import (  # noqa: E402
    CLOCK_SOURCE_EXTRA_DAYS,
    DAILY_CANDIDATE_MINUTES,
    ROUND_TRIP_COST_BPS,
)
from intrinsic_external_liquidity_v2_week import (  # noqa: E402
    CONTEXT_WARMUP_DAYS,
    NS_PER_HOUR,
    TargetFreeSweepRetestDetector,
)
from intrinsic_external_liquidity_v3_router import (  # noqa: E402
    build_open_liquidity_snapshots,
    select_target_indexed,
)
from nautilus_plan_backtest import (  # noqa: E402
    NautilusExecutionConfig,
    run_nautilus_plan_backtest,
)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_execution(path: Path) -> NautilusExecutionConfig:
    config = NautilusExecutionConfig.from_mapping(
        json.loads(path.read_text(encoding="utf-8")),
    )
    if abs(config.risk_fraction - 0.03) > 1e-12:
        raise ValueError("authoritative candidate-01 evaluations require 3% risk")
    return config


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_WARMUP_DAYS)
    clock_source_start = context_start - timedelta(days=CLOCK_SOURCE_EXTRA_DAYS)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    signal_records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        signal_records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=DAILY_CANDIDATE_MINUTES,
    )
    execution_frame, execution_records = load_interval(
        symbol="BTCUSDT",
        start=evaluation_start,
        end=evaluation_end,
        cache_dir=args.cache / "execution-klines",
        warmup_minutes=2,
    )

    feature_detector = ImpactRegimeDetector()
    detector = TargetFreeSweepRetestDetector()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        detector.on_feature(index=index, features=feature_detector.features)

    features = feature_detector.features
    cost = execution.all_in_cost_bps_per_side / 10_000.0
    signals = [
        signal
        for signal in detector.signals
        if start_ns <= signal.signal_time_ns < end_ns
    ]
    snapshots = build_open_liquidity_snapshots(
        features=features,
        events=detector.detector.events,
        signal_indices=(signal.signal_bar_index for signal in signals),
    )

    plans: list[ScenarioPlan] = []
    decisions: list[dict[str, Any]] = []
    for signal in signals:
        plan, target_index, price_fraction, net_rr = select_target_indexed(
            signal=signal,
            features=features,
            events=detector.detector.events,
            snapshot=snapshots[signal.signal_bar_index],
            cost=cost,
            minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
            minimum_net_reward_risk=execution.minimum_net_reward_risk,
        )
        accepted = plan is not None
        decisions.append(
            {
                "scenario_id": signal.scenario_id,
                "signal_time_ns": signal.signal_time_ns,
                "signal_bar_index": signal.signal_bar_index,
                "side": signal.side.value,
                "accepted": accepted,
                "reason_code": (
                    plan.reason_code
                    if accepted
                    else "NO_UNCONSUMED_LOCAL_OPPOSING_POOL_WITH_NET_GEOMETRY"
                ),
                "selected_target": plan.target_price if accepted else None,
                "selected_target_event_index": target_index,
                "expected_price_risk_fraction": price_fraction,
                "expected_net_reward_risk": net_rr,
            },
        )
        if plan is not None:
            plans.append(plan)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_plan_backtest(
        label=f"BTCUSDT-local-{evaluation_start.date().isoformat()}-7d",
        features=features,
        execution_frame=execution_frame,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )

    pd.DataFrame(asdict(row) for row in signals).to_csv(
        output / "retest_signals.csv",
        index=False,
    )
    pd.DataFrame(decisions).to_csv(output / "routing_decisions.csv", index=False)
    pd.DataFrame(asdict(row) for row in detector.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(item.to_dict() for item in calibrations).to_json(
        output / "daily_clock_calibrations.json",
        orient="records",
        indent=2,
    )

    decision_counts = Counter(row["reason_code"] for row in decisions)
    evaluation_bars = [
        row.bar for row in features if start_ns <= row.bar.end_time_ns < end_ns
    ]
    payload = {
        "candidate": "local intrinsic failed-auction rotation",
        "controlled_change": "remove only 24h/72h location gate",
        "unchanged": [
            "40-bps cost-derived directional-change structure",
            "same-side sweep and close-back re-entry",
            "aggregate-flow reversal",
            "boundary retest rejection",
            "path-extreme stop plus cost buffer",
            "nearest unconsumed opposing pivot target with cost-net geometry",
            "3% current Nautilus NAV planned loss",
            "7 bps per side all-in stress cost",
            "one global position",
        ],
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "execution_market_data": "official Binance Vision USD-M one-minute klines",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "evaluation_retest_signals": len(signals),
        "routed_plans": len(plans),
        "decision_counts": dict(decision_counts),
        "detector_counts": dict(detector.counts),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / NS_PER_HOUR,
        "metrics": evidence.metrics,
        "signal_downloads": [record.to_dict() for record in signal_records],
        "execution_downloads": [asdict(record) for record in execution_records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "intrinsic_local_liquidity_v5_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v5-local-nautilus",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v5-local-nautilus",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
