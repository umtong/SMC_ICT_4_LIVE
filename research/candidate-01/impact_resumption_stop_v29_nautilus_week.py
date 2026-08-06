#!/usr/bin/env python3
"""Evaluate v29 impact-resumption entries through NautilusTrader only.

The candidate observes a cost-resolved outside initiative and the first
completed opposite-flow pullback which preserves outside value.  The primary
rule arms a STOP_LIMIT bracket one BTC tick beyond that pullback extreme, with a
7-bp worst-fill cap.  The single ablation enters at market immediately after the
same completed pullback.  Detection, stop and target are identical.

Official Binance Vision aggregate trades are represented one-for-one as
NautilusTrader TradeTicks.  NautilusTrader exclusively owns order matching,
fees, margin, positions, PnL and account NAV.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any, Iterable

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
from impact_elasticity_resumption_v28_nautilus_week import (  # noqa: E402
    CONTEXT_DAYS,
    COST_RESOLVED_MOVE_BPS,
    FLOW_SCORE_THRESHOLD,
    PULSE_BARS,
    RESPONSE_BARS,
    STRUCTURE_BARS,
)
from impact_regime_probe import ImpactRegimeDetector  # noqa: E402
from impact_resumption_stop_state_v29 import (  # noqa: E402
    BTC_TICK_SIZE,
    STOP_LIMIT_PROTECTION_FRACTION,
    PullbackResumptionEntryStateMachine,
    evaluation_instructions,
    evaluation_plans,
)
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from nautilus_tick_stop_plan_backtest import (  # noqa: E402
    StopEntryInstruction,
    run_nautilus_tick_stop_plan_backtest,
)
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


RULES = ("stop-limit-resumption", "pullback-market-control")
NS_PER_DAY = 86_400_000_000_000
FLUSH_TICKS = 3


def execution_trade_windows(
    records: Iterable[Any],
    *,
    instructions: list[StopEntryInstruction],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    """Select outcome-independent instruction windows and daily NAV markers."""

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
    marker_days: set[int] = set()
    interval_index = 0
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
        raise RuntimeError(f"expected {FLUSH_TICKS} flush ticks, found {flush}")
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
    )
    feature_detector = ImpactRegimeDetector()
    scenario = PullbackResumptionEntryStateMachine()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        scenario.on_feature(index=index, features=feature_detector.features)

    instructions = evaluation_instructions(
        scenario.stop_instructions,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    market_plans = evaluation_plans(
        scenario.market_plans,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    if len(instructions) != len(market_plans):
        raise RuntimeError(
            "v29 primary and market-control plan counts must remain identical",
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
    if args.rule == "stop-limit-resumption":
        evidence = run_nautilus_tick_stop_plan_backtest(
            label=(
                f"BTCUSDT-v29-stop-limit-resumption-"
                f"{evaluation_start.date().isoformat()}-7d"
            ),
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
            label=(
                f"BTCUSDT-v29-pullback-market-control-"
                f"{evaluation_start.date().isoformat()}-7d"
            ),
            trades=execution_trades,
            plans=market_plans,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            execution=execution,
            maximum_hold_ns=MAXIMUM_HOLD_NS,
            output_dir=output,
        )

    pd.DataFrame(asdict(row) for row in scenario.initiatives).to_csv(
        output / "initiative_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in market_plans).to_csv(
        output / "scenario_plans.csv",
        index=False,
    )
    pd.DataFrame(
        {
            **asdict(row.plan),
            "side": row.plan.side.value,
            "trigger_price": row.trigger_price,
            "limit_price": row.limit_price,
            "expiry_time_ns": row.expiry_time_ns,
            "entry_reason": row.entry_reason,
        }
        for row in instructions
    ).to_csv(output / "stop_entry_instructions.csv", index=False)
    pd.DataFrame(asdict(row) for row in scenario.entry_decisions).to_csv(
        output / "entry_decisions.csv",
        index=False,
    )
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [row.to_dict() for row in calibrations]},
    )

    metrics = evidence.metrics
    payload = {
        "candidate": "outside impact pullback conditional resumption entry",
        "candidate_version": 29,
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "structure_bars": STRUCTURE_BARS,
        "pulse_bars": PULSE_BARS,
        "flow_score_threshold": FLOW_SCORE_THRESHOLD,
        "cost_resolved_move_bps": COST_RESOLVED_MOVE_BPS,
        "response_bars": RESPONSE_BARS,
        "btc_tick_size": BTC_TICK_SIZE,
        "stop_limit_protection_bps": (
            STOP_LIMIT_PROTECTION_FRACTION * 10_000.0
        ),
        "state_counts": dict(scenario.counts),
        "initiative_event_count": len(scenario.initiatives),
        "selected_plan_count": len(market_plans),
        "selected_instruction_count": len(instructions),
        "entry_order_type": (
            "STOP_LIMIT"
            if args.rule == "stop-limit-resumption"
            else "MARKET"
        ),
        "entry_contract": (
            "arm after the first completed counterflow pullback which retains "
            "outside value; trigger one BTC tick beyond that completed pullback "
            "extreme; permit no worse than 7bp beyond trigger; size risk at "
            "that worst limit"
            if args.rule == "stop-limit-resumption"
            else "enter on the first venue trade after the same completed "
            "counterflow pullback"
        ),
        "official_execution_trade_ticks": len(execution_trades),
        "execution_tick_windows": [list(row) for row in windows],
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_resumption_stop_v29_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v29-stop-entry",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v29-stop-entry",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
