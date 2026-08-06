#!/usr/bin/env python3
"""Authoritative Nautilus evaluation of v23 MSS displacement entry.

`mss-displacement` freezes v22's causal calendar target hierarchy but emits the
plan at the completed MSS event and uses the full failed-sweep-to-MSS adverse
path as structural invalidation. `retest-control` runs the v22 retest-entry
scenario on the identical event stream.

Official Binance Vision aggregate trades are represented one-for-one as
NautilusTrader TradeTicks. NautilusTrader exclusively owns order matching,
contingent exits, commissions, margin, positions, account equity and reports.
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
from aggtrade_data import AggTrade, download_aggtrade_days, iter_downloads  # noqa: E402
from calendar_mss_displacement_v23 import (  # noqa: E402
    CalendarMssDisplacementStateMachine,
)
from calendar_target_mss_retest_v22 import (  # noqa: E402
    CalendarTargetMssRetestStateMachine,
)
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    MAXIMUM_HOLD_NS,
    ROUND_TRIP_COST_BPS,
)
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import (  # noqa: E402
    FLUSH_TICKS,
    NS_PER_DAY,
    atomic_json,
    load_execution,
)

RULES = ("mss-displacement", "retest-control")
CONTEXT_DAYS = 8
CLOCK_MINUTES = (1,)


def execution_trade_windows(
    records: list[Any],
    *,
    plans: list[ScenarioPlan],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    before_ns = 60_000_000_000
    after_ns = 120_000_000_000
    intervals = sorted(
        (
            max(start_ns, int(plan.signal_time_ns) - before_ns),
            min(end_ns - 1, int(plan.signal_time_ns) + maximum_hold_ns + after_ns),
        )
        for plan in plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    )
    merged: list[tuple[int, int]] = []
    for left, right in intervals:
        if right < left:
            continue
        if not merged or left > merged[-1][1] + 1:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))

    selected: list[AggTrade] = []
    interval_index = 0
    marker_days: set[int] = set()
    flush = 0
    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            if flush < FLUSH_TICKS:
                selected.append(trade)
                flush += 1
                continue
            break
        day_id = ts_ns // NS_PER_DAY
        if day_id not in marker_days:
            marker_days.add(day_id)
            selected.append(trade)
            continue
        while interval_index < len(merged) and ts_ns > merged[interval_index][1]:
            interval_index += 1
        if (
            interval_index < len(merged)
            and merged[interval_index][0] <= ts_ns <= merged[interval_index][1]
        ):
            selected.append(trade)

    expected_days = (end_ns - start_ns) // NS_PER_DAY
    if len(marker_days) != expected_days:
        raise RuntimeError(
            f"expected {expected_days} UTC markers, found {len(marker_days)}",
        )
    if flush != FLUSH_TICKS:
        raise RuntimeError(f"expected {FLUSH_TICKS} flush ticks, found {flush}")
    return selected, merged


def build_plans(
    bars: list[Any],
    *,
    start_ns: int,
    end_ns: int,
    rule: str,
) -> tuple[ImpactRegimeDetector, Any, list[ScenarioPlan]]:
    feature_detector = ImpactRegimeDetector()
    scenario: Any = (
        CalendarMssDisplacementStateMachine()
        if rule == "mss-displacement"
        else CalendarTargetMssRetestStateMachine()
    )
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        scenario.on_feature(index=index, features=feature_detector.features)
    plans = [
        plan
        for plan in scenario.plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    ]
    return feature_detector, scenario, plans


def write_candidate_evidence(output: Path, scenario: Any) -> dict[str, Any]:
    book = scenario.calendar_book
    pd.DataFrame(level.to_dict() for level in book.levels).to_csv(
        output / "calendar_liquidity_levels.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in book.events).to_csv(
        output / "calendar_liquidity_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.target_selections).to_csv(
        output / "calendar_target_selections.csv",
        index=False,
    )
    if hasattr(scenario, "displacement_diagnostics"):
        pd.DataFrame(
            asdict(row) for row in scenario.displacement_diagnostics
        ).to_csv(output / "mss_displacement_diagnostics.csv", index=False)
        displacement_count = len(scenario.displacement_diagnostics)
    else:
        pd.DataFrame(
            columns=[
                "scenario_id", "signal_time_ns", "side",
                "failed_sweep_boundary", "broken_internal_pivot",
                "calendar_target", "mss_close", "path_high", "path_low",
                "structural_stop", "price_risk_bps_at_mss_close",
                "gross_reward_bps_at_mss_close",
            ],
        ).to_csv(output / "mss_displacement_diagnostics.csv", index=False)
        displacement_count = 0
    return {
        "calendar_levels": len(book.levels),
        "active_calendar_levels_at_end": sum(
            1 for level in book.levels if level.active
        ),
        "calendar_target_selections": len(scenario.target_selections),
        "selected_target_periods": dict(
            Counter(row.target_period for row in scenario.target_selections)
        ),
        "skipped_incomplete_weeks": book.skipped_incomplete_weeks,
        "mss_displacement_diagnostics": displacement_count,
    }


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_DAYS)
    clock_source_start = context_start - timedelta(days=1)
    download_end = evaluation_end + timedelta(minutes=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=download_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
        candidate_minutes=CLOCK_MINUTES,
    )
    feature_detector, scenario, plans = build_plans(
        bars,
        start_ns=start_ns,
        end_ns=end_ns,
        rule=args.rule,
    )
    execution_trades, windows = execution_trade_windows(
        records,
        plans=plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=f"BTCUSDT-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        trades=execution_trades,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )

    pd.DataFrame(asdict(row) for row in scenario.detector.events).to_csv(
        output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(plan) for plan in plans).to_csv(
        output / "scenario_plans.csv",
        index=False,
    )
    candidate_diagnostics = write_candidate_evidence(output, scenario)
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [item.to_dict() for item in calibrations]},
    )

    payload = {
        "candidate": "calendar-target failed sweep entered at MSS displacement",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "clock_minutes": CLOCK_MINUTES[0],
        "scenario_contract": (
            "failed sweep -> completed aligned-flow MSS -> first later venue "
            "TradeTick entry; stop beyond the full failed-sweep-to-MSS path; "
            "causal day/week calendar target"
            if args.rule == "mss-displacement"
            else "v22 calendar-target broken-pivot-retest control on identical bars"
        ),
        "scenario_counts": dict(scenario.counts),
        "selected_plan_count": len(plans),
        "selected_response_counts": dict(Counter(plan.response for plan in plans)),
        **candidate_diagnostics,
        "official_execution_trade_ticks": len(execution_trades),
        "execution_tick_windows": [list(item) for item in windows],
        "tick_selection": (
            "outcome-independent plan windows plus first official trade of "
            "each evaluation UTC day for NAV marking"
        ),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": evidence.metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "calendar_mss_displacement_v23_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v23-mss-displacement",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v23-mss-displacement",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
