#!/usr/bin/env python3
"""Resolved-impact plans with causal boundary-retest resting entries.

The v17 initiative, three-notional resolution, stop and measured target remain
unchanged. The only candidate variable is entry execution.

``boundary-limit`` arms a NautilusTrader limit bracket at the completed
confirmation boundary. The order remains valid for the same wall-clock duration
that the just-completed initiative-to-resolution process required. It is
canceled when the measured target trades first or that causal response duration
expires.

``market-control`` submits the unchanged plan on the first venue trade after the
completed resolution. Both modes use identical official Binance Vision
aggregate trades represented one-for-one as NautilusTrader TradeTicks. Fills,
orders, fees, margin, positions and NAV are exclusively engine-owned.
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
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from impact_regime_probe import ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from nautilus_tick_limit_plan_backtest import (  # noqa: E402
    RestingEntryInstruction,
    run_nautilus_tick_limit_plan_backtest,
)
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import (  # noqa: E402
    CLOCK_MINUTES,
    CONTEXT_DAYS,
    atomic_json,
    build_plans,
    load_execution,
)

RULES = ("boundary-limit", "market-control")
FLUSH_TICKS = 3
NS_PER_DAY = 86_400_000_000_000


def source_scenario_id(plan: ScenarioPlan) -> str:
    marker = ":resolved-"
    if marker not in plan.scenario_id:
        raise ValueError(f"not a resolved-impact plan: {plan.scenario_id}")
    return plan.scenario_id.split(marker, 1)[0]


def build_instructions(
    *,
    plans: list[ScenarioPlan],
    transitions: list[Any],
) -> tuple[list[RestingEntryInstruction], list[dict[str, Any]]]:
    armed_time_by_source = {
        str(row.scenario_id): int(row.event_time_ns)
        for row in transitions
        if str(row.event_type) == "ARMED"
    }
    instructions: list[RestingEntryInstruction] = []
    diagnostics: list[dict[str, Any]] = []
    for plan in plans:
        source = source_scenario_id(plan)
        if source not in armed_time_by_source:
            raise RuntimeError(f"missing causal ARMED transition for {source}")
        armed_time_ns = armed_time_by_source[source]
        resolution_duration_ns = int(plan.signal_time_ns) - armed_time_ns
        if resolution_duration_ns <= 0:
            raise RuntimeError(
                f"non-positive resolution duration for {plan.scenario_id}",
            )
        expiry_time_ns = int(plan.signal_time_ns) + resolution_duration_ns
        instruction = RestingEntryInstruction(
            plan=plan,
            entry_price=float(plan.confirmation_hold_price),
            expiry_time_ns=expiry_time_ns,
            entry_reason="CAUSAL_CONFIRMATION_BOUNDARY_RETEST",
        )
        instructions.append(instruction)
        diagnostics.append(
            {
                "scenario_id": plan.scenario_id,
                "source_scenario_id": source,
                "response": plan.response,
                "side": plan.side.value,
                "signal_time_ns": int(plan.signal_time_ns),
                "initiative_armed_time_ns": armed_time_ns,
                "resolution_duration_ns": resolution_duration_ns,
                "planned_entry_price": float(plan.confirmation_hold_price),
                "expiry_time_ns": expiry_time_ns,
                "stop_price": float(plan.stop_price),
                "target_price": float(plan.target_price),
            },
        )
    return instructions, diagnostics


def execution_trade_windows(
    records: list[Any],
    *,
    instructions: list[RestingEntryInstruction],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    """Keep plan/entry windows and one official NAV marker per UTC day."""

    before_ns = 60_000_000_000
    after_ns = 120_000_000_000
    intervals = sorted(
        (
            max(start_ns, int(item.plan.signal_time_ns) - before_ns),
            min(
                end_ns - 1,
                int(item.expiry_time_ns) + maximum_hold_ns + after_ns,
            ),
        )
        for item in instructions
        if start_ns <= int(item.plan.signal_time_ns) < end_ns
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
            f"expected {expected_days} daily markers, found {len(marker_days)}",
        )
    if flush != FLUSH_TICKS:
        raise RuntimeError(f"expected {FLUSH_TICKS} flush trades, found {flush}")
    return selected, merged


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
    detector, resolver, plans = build_plans(
        bars,
        start_ns=start_ns,
        end_ns=end_ns,
        rule="resolved-full",
    )
    instructions, instruction_diagnostics = build_instructions(
        plans=plans,
        transitions=resolver.transitions,
    )
    execution_trades, windows = execution_trade_windows(
        records,
        instructions=instructions,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    if args.rule == "boundary-limit":
        evidence = run_nautilus_tick_limit_plan_backtest(
            label=f"BTCUSDT-boundary-limit-{evaluation_start.date().isoformat()}-7d",
            trades=execution_trades,
            instructions=instructions,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            execution=execution,
            maximum_hold_ns=MAXIMUM_HOLD_NS,
            output_dir=output,
        )
    else:
        evidence = run_nautilus_tick_plan_backtest(
            label=f"BTCUSDT-market-control-{evaluation_start.date().isoformat()}-7d",
            trades=execution_trades,
            plans=plans,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            execution=execution,
            maximum_hold_ns=MAXIMUM_HOLD_NS,
            output_dir=output,
        )

    pd.DataFrame(asdict(row) for row in detector.pulse_events).to_csv(
        output / "pulse_events.csv", index=False,
    )
    pd.DataFrame(asdict(row) for row in resolver.transitions).to_csv(
        output / "resolution_transitions.csv", index=False,
    )
    pd.DataFrame(asdict(plan) for plan in plans).to_csv(
        output / "resolved_plans.csv", index=False,
    )
    pd.DataFrame(instruction_diagnostics).to_csv(
        output / "entry_instructions.csv", index=False,
    )
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [item.to_dict() for item in calibrations]},
    )

    metrics = evidence.metrics
    payload = {
        "candidate": "resolved impact boundary-retest entry",
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
        "initiative_plans_in_stream": len(detector.continuation_plans),
        "resolution_counts": dict(resolver.counts),
        "resolved_plan_count": len(plans),
        "response_counts": dict(Counter(plan.response for plan in plans)),
        "entry_instruction_count": len(instructions),
        "entry_contract": (
            "confirmation boundary limit valid for the same wall-clock duration "
            "as initiative-to-resolution; cancel on target-first or expiry"
            if args.rule == "boundary-limit"
            else "first venue trade after completed resolution"
        ),
        "official_execution_trade_ticks": len(execution_trades),
        "execution_tick_windows": [list(item) for item in windows],
        "tick_selection": (
            "outcome-independent resolution/entry/hold windows plus first "
            "official trade of each evaluation UTC day for NAV marking"
        ),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "limit_entries_expired": int(metrics.get("limit_entries_expired", 0)),
        "targets_consumed_before_entry": int(
            metrics.get("targets_consumed_before_entry", 0),
        ),
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "boundary_retest_resolved_impact_v19_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v19-boundary-retest",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v19-boundary-retest",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
