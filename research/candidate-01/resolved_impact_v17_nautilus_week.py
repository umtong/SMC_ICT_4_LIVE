#!/usr/bin/env python3
"""Causal resolved-impact portfolio executed only by NautilusTrader TradeTicks.

A strong aggressive-flow initiative which efficiently retains value beyond a
completed 20-event structure is an observation, not an entry.  The market is
given three additional equal-notional events (three times the current day's
causally calibrated 20-minute quote-notional clock) to reveal one of two
mutually exclusive responses:

* failed impact: outside value is lost, opposite aggressive flow appears and
  price crosses the initiative midpoint -> reverse toward the opposite edge of
  the pre-initiative structure;
* durable impact: failure never occurs, at least two response closes retain
  outside value and cumulative aligned flow stays positive -> continue toward
  the measured external projection.

The daily information clock is fixed from the immediately preceding completed
UTC day. Three prior context days are processed before evaluation so robust
flow and structure state is live at the first evaluation trade. Candidate logic
only creates immutable ScenarioPlan objects. Official Binance Vision aggregate
trades are represented one-for-one as NautilusTrader TradeTick objects; fills,
brackets, fees, margin, positions and NAV are exclusively engine-owned.
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
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from impact_resolution_candidate import ImpactResolutionStateMachine  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from nautilus_plan_backtest import NautilusExecutionConfig  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402

RULES = ("resolved-full", "resolved-reversal-only")
CONTEXT_DAYS = 3
CLOCK_MINUTES = (20,)
FLUSH_TICKS = 3
NS_PER_DAY = 86_400_000_000_000


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


def execution_trade_windows(
    records: list[Any],
    *,
    plans: list[ScenarioPlan],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    """Keep outcome-independent plan windows and one NAV marker per UTC day."""

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

    result: list[AggTrade] = []
    interval_index = 0
    flush = 0
    marker_days: set[int] = set()
    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            if flush < FLUSH_TICKS:
                result.append(trade)
                flush += 1
                continue
            break

        day_id = ts_ns // NS_PER_DAY
        if day_id not in marker_days:
            marker_days.add(day_id)
            result.append(trade)
            continue

        while interval_index < len(merged) and ts_ns > merged[interval_index][1]:
            interval_index += 1
        if (
            interval_index < len(merged)
            and merged[interval_index][0] <= ts_ns <= merged[interval_index][1]
        ):
            result.append(trade)

    expected_days = (end_ns - start_ns) // NS_PER_DAY
    if len(marker_days) != expected_days:
        raise RuntimeError(
            f"expected {expected_days} UTC markers, found {len(marker_days)}",
        )
    if flush != FLUSH_TICKS:
        raise RuntimeError(f"expected {FLUSH_TICKS} flush ticks, found {flush}")
    return result, merged


def build_plans(
    bars: list[Any],
    *,
    start_ns: int,
    end_ns: int,
    rule: str,
) -> tuple[
    ImpactRegimeDetector,
    ImpactResolutionStateMachine,
    list[ScenarioPlan],
]:
    detector = ImpactRegimeDetector()
    resolver = ImpactResolutionStateMachine()
    previous_initiatives = 0
    for index, bar in enumerate(bars):
        detector.on_bar(bar)
        new_initiatives = detector.continuation_plans[previous_initiatives:]
        previous_initiatives = len(detector.continuation_plans)
        resolver.on_feature(
            index=index,
            feature=detector.features[-1],
            new_initiative_plans=new_initiatives,
        )

    plans = [
        plan
        for plan in resolver.plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    ]
    if rule == "resolved-reversal-only":
        plans = [plan for plan in plans if plan.response == "EXHAUSTION_REVERSAL"]
    return detector, resolver, plans


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

    pd.DataFrame(asdict(row) for row in detector.pulse_events).to_csv(
        output / "pulse_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in resolver.transitions).to_csv(
        output / "resolution_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(plan) for plan in plans).to_csv(
        output / "resolved_plans.csv",
        index=False,
    )
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [item.to_dict() for item in calibrations]},
    )

    response_counts = Counter(plan.response for plan in plans)
    payload = {
        "candidate": "causal three-notional resolved impact",
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
        "clock_source": "immediately preceding completed UTC day",
        "initiative_plans_in_stream": len(detector.continuation_plans),
        "resolution_counts": dict(resolver.counts),
        "selected_plan_count": len(plans),
        "selected_response_counts": dict(response_counts),
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
    atomic_json(output / "resolved_impact_v17_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v17-resolved-impact",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v17-resolved-impact",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
