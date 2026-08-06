#!/usr/bin/env python3
"""Evaluate v31 failed-acceptance reversals through NautilusTrader only.

Primary: after an outside-flow initiative and completed counterflow pullback,
arm a reversal STOP_LIMIT one BTC tick through the accepted boundary.  The
single control removes only that boundary-loss confirmation and enters the same
reversal at market immediately after the completed pullback.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys

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
from failed_acceptance_reversal_state_v31 import (  # noqa: E402
    BTC_TICK_SIZE,
    STOP_LIMIT_PROTECTION_FRACTION,
    FailedAcceptanceReversalStateMachine,
)
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
    evaluation_instructions,
    evaluation_plans,
)
from impact_resumption_stop_v29_nautilus_week import (  # noqa: E402
    execution_trade_windows,
)
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from nautilus_tick_stop_plan_backtest import (  # noqa: E402
    run_nautilus_tick_stop_plan_backtest,
)
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


RULES = ("boundary-loss-reversal", "immediate-reversal-control")


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
    scenario = FailedAcceptanceReversalStateMachine()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        scenario.on_feature(index=index, features=feature_detector.features)

    instructions = evaluation_instructions(
        scenario.stop_instructions,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    primary_plans = evaluation_plans(
        scenario.market_plans,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    control_plans = evaluation_plans(
        scenario.immediate_control_plans,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    if not (
        len(instructions) == len(primary_plans) == len(control_plans)
    ):
        raise RuntimeError("v31 primary and control opportunity counts diverged")

    execution_trades, windows = execution_trade_windows(
        records,
        instructions=instructions,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    if args.rule == "boundary-loss-reversal":
        selected_plans = primary_plans
        evidence = run_nautilus_tick_stop_plan_backtest(
            label=(
                f"BTCUSDT-v31-boundary-loss-reversal-"
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
        entry_order_type = "STOP_LIMIT"
    else:
        selected_plans = control_plans
        evidence = run_nautilus_tick_plan_backtest(
            label=(
                f"BTCUSDT-v31-immediate-reversal-control-"
                f"{evaluation_start.date().isoformat()}-7d"
            ),
            trades=execution_trades,
            plans=control_plans,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            execution=execution,
            maximum_hold_ns=MAXIMUM_HOLD_NS,
            output_dir=output,
        )
        entry_order_type = "MARKET"

    pd.DataFrame(asdict(row) for row in scenario.initiatives).to_csv(
        output / "initiative_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in selected_plans).to_csv(
        output / "scenario_plans.csv",
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

    payload = {
        "candidate": "accepted pullback followed by failed-boundary reversal",
        "candidate_version": 31,
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
        "source_state": (
            "cost-resolved outside-flow initiative followed by first completed "
            "counterflow pullback which closes outside the accepted boundary"
        ),
        "direction_resolution": (
            "primary reverses only after a later TradeTick crosses the accepted "
            "boundary; control reverses immediately after the completed pullback"
        ),
        "invalidation_contract": (
            "completed pullback outside extreme plus one 7bp side-cost buffer"
        ),
        "target_contract": (
            "opposite edge of the completed pre-initiative 20-event structure"
        ),
        "btc_tick_size": BTC_TICK_SIZE,
        "stop_limit_protection_bps": (
            STOP_LIMIT_PROTECTION_FRACTION * 10_000.0
        ),
        "state_counts": dict(scenario.counts),
        "initiative_event_count": len(scenario.initiatives),
        "selected_plan_count": len(selected_plans),
        "selected_instruction_count": len(instructions),
        "entry_order_type": entry_order_type,
        "official_execution_trade_ticks": len(execution_trades),
        "execution_tick_windows": [list(row) for row in windows],
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": evidence.metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "failed_acceptance_reversal_v31_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v31-failed-acceptance",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v31-failed-acceptance",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
