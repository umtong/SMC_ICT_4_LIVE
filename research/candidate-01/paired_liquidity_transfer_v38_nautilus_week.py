#!/usr/bin/env python3
"""Authoritative BTC week for v38 paired-liquidity transfer.

A single causal plan stream is executed twice on identical official Binance
Vision aggregate trades:

* primary: a resting limit at the MSS FVG consequent-encroachment midpoint;
* control: first later venue TradeTick market entry.

NautilusTrader exclusively owns matching, contingent exits, commissions,
margin, positions, account equity and reports.
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
from calendar_mss_displacement_v23_nautilus_week import (  # noqa: E402
    execution_trade_windows,
)
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import (  # noqa: E402
    MAXIMUM_HOLD_NS,
    ROUND_TRIP_COST_BPS,
)
from impact_regime_probe import ImpactRegimeDetector  # noqa: E402
from nautilus_tick_limit_plan_backtest import (  # noqa: E402
    run_nautilus_tick_limit_plan_backtest,
)
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from paired_liquidity_transfer_v38 import (  # noqa: E402
    PairedLiquidityTransferStateMachine,
)
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402

CONTEXT_DAYS = 8
CLOCK_MINUTES = (1,)


def build_candidate(
    bars: list[Any],
    *,
    start_ns: int,
    end_ns: int,
) -> tuple[ImpactRegimeDetector, PairedLiquidityTransferStateMachine]:
    feature_detector = ImpactRegimeDetector()
    scenario = PairedLiquidityTransferStateMachine()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        scenario.on_feature(index=index, features=feature_detector.features)
    scenario.plans[:] = [
        plan
        for plan in scenario.plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    ]
    scenario.instructions[:] = [
        instruction
        for instruction in scenario.instructions
        if start_ns <= int(instruction.plan.signal_time_ns) < end_ns
    ]
    selected_ids = {plan.scenario_id for plan in scenario.plans}
    scenario.transfer_diagnostics[:] = [
        row for row in scenario.transfer_diagnostics if row.scenario_id in selected_ids
    ]
    return feature_detector, scenario


def _metric(metrics: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = metrics.get(key, default)
    return float(default if value is None else value)


def _decision(primary: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    trades = int(primary.get("closed_positions", primary.get("positions", 0)) or 0)
    geometric = _metric(primary, "geometric_mean_daily_return")
    drawdown = _metric(primary, "max_drawdown")
    primary_return = _metric(primary, "total_return")
    control_return = _metric(control, "total_return")
    operational = (
        int(primary.get("gate_violations", 0) or 0) == 0
        and int(primary.get("protective_order_failures", 0) or 0) == 0
        and bool(primary.get("ended_flat", True))
    )
    target_met = geometric >= 0.01
    enough_trades = trades >= 4
    primary_better = primary_return > control_return
    recoverable = drawdown > -0.20
    return {
        "advance": bool(
            operational
            and target_met
            and enough_trades
            and primary_better
            and recoverable
        ),
        "operational_contract_passed": operational,
        "target_geometric_daily_return_met": target_met,
        "minimum_four_closed_trades_met": enough_trades,
        "primary_outperformed_market_control": primary_better,
        "drawdown_below_twenty_percent": recoverable,
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
    feature_detector, scenario = build_candidate(
        bars,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    execution_trades, windows = execution_trade_windows(
        records,
        plans=scenario.plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )

    output = args.output
    primary_dir = output / "fvg-limit-primary"
    control_dir = output / "market-control"
    output.mkdir(parents=True, exist_ok=True)
    primary = run_nautilus_tick_limit_plan_backtest(
        label=f"BTCUSDT-v38-fvg-limit-{evaluation_start.date().isoformat()}-7d",
        trades=execution_trades,
        instructions=scenario.instructions,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=primary_dir,
    )
    control = run_nautilus_tick_plan_backtest(
        label=f"BTCUSDT-v38-market-control-{evaluation_start.date().isoformat()}-7d",
        trades=execution_trades,
        plans=scenario.plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=control_dir,
    )

    pd.DataFrame(asdict(plan) for plan in scenario.plans).to_csv(
        output / "scenario_plans.csv",
        index=False,
    )
    pd.DataFrame(
        {
            **asdict(item.plan),
            "side": item.plan.side.value,
            "entry_price": item.entry_price,
            "expiry_time_ns": item.expiry_time_ns,
            "entry_reason": item.entry_reason,
        }
        for item in scenario.instructions
    ).to_csv(output / "fvg_limit_instructions.csv", index=False)
    pd.DataFrame(asdict(row) for row in scenario.transfer_diagnostics).to_csv(
        output / "transfer_diagnostics.csv",
        index=False,
    )

    decision = _decision(primary.metrics, control.metrics)
    payload = {
        "candidate": "paired external-liquidity transfer after saturated sweep",
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "official Binance Vision aggTrades as TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "frozen_random_seed": 3801,
        "frozen_week_rank": args.week_rank,
        "scenario_contract": (
            "failed sweep -> terminal effort without result -> aligned MSS with "
            "expanded three-event FVG -> FVG midpoint resting entry -> frozen "
            "causal day/week external-liquidity target"
        ),
        "scenario_counts": dict(scenario.counts),
        "selected_plan_count": len(scenario.plans),
        "selected_side_counts": dict(
            Counter(plan.side.value for plan in scenario.plans)
        ),
        "saturation_evidence_count": len(scenario.saturation_evidence),
        "selected_transfer_count": len(scenario.transfer_diagnostics),
        "calendar_target_selections_total": len(scenario.target_selections),
        "official_execution_trade_ticks": len(execution_trades),
        "execution_tick_windows": [list(item) for item in windows],
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "primary_metrics": primary.metrics,
        "market_control_metrics": control.metrics,
        "decision": decision,
        "daily_clock_calibrations": [item.to_dict() for item in calibrations],
        "feature_count": len(feature_detector.features),
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "paired_liquidity_transfer_v38_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--week-rank", type=int, required=True)
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v38-paired-transfer",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v38-paired-transfer",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
