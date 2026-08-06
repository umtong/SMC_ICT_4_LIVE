#!/usr/bin/env python3
"""Authoritative v35 failed-sweep positioning-cycle evaluation.

Both rules freeze v23's cost-resolved failed sweep, aligned-flow MSS,
full sweep-to-MSS structural invalidation and causal completed-day/week target.
The primary adds one causal market-state variable: official open interest must
expand into the swept-direction initiative leg and contract by the completed
MSS. The control removes only this positioning-cycle confirmation.

Official aggregate trades are represented one-for-one as NautilusTrader
TradeTicks. NautilusTrader exclusively owns orders, fills, commissions, margin,
positions, PnL, account equity and reports.
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
from calendar_mss_displacement_v23 import (  # noqa: E402
    CalendarMssDisplacementStateMachine,
)
from calendar_mss_displacement_v23_nautilus_week import (  # noqa: E402
    CLOCK_MINUTES,
    CONTEXT_DAYS,
    execution_trade_windows,
    write_candidate_evidence,
)
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    MAXIMUM_HOLD_NS,
    ROUND_TRIP_COST_BPS,
)
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from positioning_cycle_filter_v35 import (  # noqa: E402
    build_positioning_cycle_plans,
)
from positioning_metrics_data_v35 import (  # noqa: E402
    METRIC_INTERVAL_NS,
    download_position_metric_days,
    load_position_metric_book,
)
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


RULES = ("oi-build-release-primary", "v23-mss-control")
SUMMARY = "positioning_cycle_mss_v35_summary.json"


def evaluation_plans(
    rows: list[ScenarioPlan],
    *,
    start_ns: int,
    end_ns: int,
) -> list[ScenarioPlan]:
    return [
        row
        for row in rows
        if start_ns <= int(row.signal_time_ns) < end_ns
    ]


def build_v23_scenario(
    bars: list[Any],
) -> tuple[ImpactRegimeDetector, CalendarMssDisplacementStateMachine]:
    detector = ImpactRegimeDetector()
    scenario = CalendarMssDisplacementStateMachine()
    for bar in bars:
        detector.on_bar(bar)
        index = len(detector.features) - 1
        scenario.on_feature(index=index, features=detector.features)
    return detector, scenario


def write_v35_evidence(
    output: Path,
    *,
    detector: ImpactRegimeDetector,
    scenario: CalendarMssDisplacementStateMachine,
    metric_book: Any,
    metric_downloads: list[Any],
    source_plans: list[ScenarioPlan],
    primary_plans: list[ScenarioPlan],
    control_plans: list[ScenarioPlan],
    selected_plans: list[ScenarioPlan],
    diagnostics: list[Any],
) -> dict[str, Any]:
    pd.DataFrame(asdict(row) for row in scenario.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in source_plans).to_csv(
        output / "source_v23_plans.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in primary_plans).to_csv(
        output / "primary_plans.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in control_plans).to_csv(
        output / "control_plans.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in selected_plans).to_csv(
        output / "scenario_plans.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in diagnostics).to_csv(
        output / "positioning_cycle_diagnostics.csv",
        index=False,
    )
    pd.DataFrame(row.to_dict() for row in metric_book.rows).to_csv(
        output / "positioning_metrics.csv",
        index=False,
    )
    atomic_json(
        output / "positioning_metric_downloads.json",
        {"downloads": [row.to_dict() for row in metric_downloads]},
    )
    v23 = write_candidate_evidence(output, scenario)
    return {
        **v23,
        "feature_count": len(detector.features),
        "positioning_metric_observations": len(metric_book.rows),
        "positioning_metric_download_count": len(metric_downloads),
        "positioning_metric_checksums_match": all(
            row.sha256 == row.expected_sha256 for row in metric_downloads
        ),
        "source_v23_plan_count": len(source_plans),
        "primary_plan_count": len(primary_plans),
        "control_plan_count": len(control_plans),
        "positioning_cycle_confirmed_count": sum(
            row.positioning_cycle_confirmed for row in diagnostics
        ),
        "positioning_reason_counts": dict(
            Counter(row.reason_code for row in diagnostics)
        ),
    }


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_DAYS)
    clock_source_start = context_start - timedelta(days=1)
    download_end = evaluation_end + timedelta(minutes=1)
    metric_start = context_start - timedelta(minutes=10)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=download_end,
        cache_dir=args.cache / "aggtrades",
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=CLOCK_MINUTES,
    )
    detector, scenario = build_v23_scenario(bars)
    source_plans = evaluation_plans(
        scenario.plans,
        start_ns=start_ns,
        end_ns=end_ns,
    )

    metric_downloads = download_position_metric_days(
        symbol="BTCUSDT",
        start=metric_start,
        end=evaluation_end,
        cache_dir=args.cache / "metrics",
        workers=args.workers,
    )
    metric_book = load_position_metric_book(metric_downloads)
    primary_plans, control_plans, diagnostics, positioning_counts = (
        build_positioning_cycle_plans(
            source_plans=source_plans,
            transitions=scenario.transitions,
            metrics=metric_book,
        )
    )
    if len(control_plans) != len(source_plans):
        raise RuntimeError("v35 control must preserve every evaluation v23 plan")
    if len(primary_plans) > len(control_plans):
        raise RuntimeError("v35 primary cannot exceed its frozen v23 control")
    selected_plans = (
        primary_plans
        if args.rule == "oi-build-release-primary"
        else control_plans
    )

    union = {
        plan.scenario_id: plan
        for plan in [*primary_plans, *control_plans]
    }
    union_plans = sorted(
        union.values(),
        key=lambda row: (row.signal_time_ns, row.scenario_id),
    )
    execution_trades, windows = execution_trade_windows(
        records,
        plans=union_plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=(
            f"BTCUSDT-v35-{args.rule}-"
            f"{evaluation_start.date().isoformat()}-7d"
        ),
        trades=execution_trades,
        plans=selected_plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )
    diagnostics_summary = write_v35_evidence(
        output,
        detector=detector,
        scenario=scenario,
        metric_book=metric_book,
        metric_downloads=metric_downloads,
        source_plans=source_plans,
        primary_plans=primary_plans,
        control_plans=control_plans,
        selected_plans=selected_plans,
        diagnostics=diagnostics,
    )
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [row.to_dict() for row in calibrations]},
    )

    payload = {
        "candidate": "position-build then release confirmed failed-sweep MSS",
        "candidate_version": 35,
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "confirmation_data": "official Binance Vision USD-M BTCUSDT metrics",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "clock_minutes": CLOCK_MINUTES[0],
        "positioning_metric_interval_minutes": METRIC_INTERVAL_NS / 60_000_000_000,
        "positioning_metric_causal_delay_minutes": (
            METRIC_INTERVAL_NS / 60_000_000_000
        ),
        "source_scenario": (
            "v23 failed 40bp intrinsic sweep -> completed aligned-flow MSS -> "
            "first later venue TradeTick; full sweep-to-MSS stop; nearest active "
            "unconsumed completed-day/week external-liquidity target"
        ),
        "primary_variable": (
            "official sum_open_interest strictly expands between the last two "
            "causally available five-minute observations into the failed sweep, "
            "then strictly contracts at a later causally available observation "
            "by the completed MSS"
            if args.rule == "oi-build-release-primary"
            else "single ablation removes only official open-interest cycle confirmation"
        ),
        "positioning_interpretation": (
            "direction comes from the frozen aggressive-flow failed sweep; OI "
            "expansion identifies position creation and later OI contraction "
            "identifies release before entry"
        ),
        "threshold_policy": (
            "strict sign and event order only; no PnL-fitted OI magnitude, ratio, "
            "session or volatility threshold"
        ),
        "scenario_counts": dict(scenario.counts),
        "positioning_counts": dict(positioning_counts),
        "selected_plan_count": len(selected_plans),
        "selected_response_counts": dict(
            Counter(plan.response for plan in selected_plans)
        ),
        **diagnostics_summary,
        "official_execution_trade_ticks": len(execution_trades),
        "execution_tick_windows": [list(row) for row in windows],
        "tick_selection": (
            "outcome-independent union-plan windows plus first official trade "
            "of each evaluation UTC day for NAV marking"
        ),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": evidence.metrics,
        "aggtrade_downloads": [row.to_dict() for row in records],
        "positioning_metric_downloads": [
            row.to_dict() for row in metric_downloads
        ],
        "long_evaluation_run": False,
    }
    atomic_json(output / SUMMARY, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--rule", required=True, choices=RULES)
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v35-positioning-cycle",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v35-positioning-cycle",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
