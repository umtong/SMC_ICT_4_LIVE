#!/usr/bin/env python3
"""Authoritative v33 two-sided pullback-auction evaluation.

Primary and control share the same outside-flow initiative, accepted first
counterflow pullback, branch-specific pullback stops, first completed structural
resolution, calendar target and NautilusTrader execution. Primary additionally
requires aggressive flow on the winning resolution event to agree with the
resolved direction. Control removes only that agreement requirement.
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
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from impact_elasticity_resumption_v28_nautilus_week import (  # noqa: E402
    CONTEXT_DAYS,
    COST_RESOLVED_MOVE_BPS,
    FLOW_SCORE_THRESHOLD,
    PULSE_BARS,
    RESPONSE_BARS,
    STRUCTURE_BARS,
)
from impact_regime_probe import ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402
from two_sided_pullback_auction_state_v33 import (  # noqa: E402
    TwoSidedPullbackAuctionStateMachine,
)


RULES = ("first-resolution-aligned-flow", "first-resolution-close-control")


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


def write_candidate_evidence(
    output: Path,
    scenario: TwoSidedPullbackAuctionStateMachine,
    *,
    selected_plans: list[ScenarioPlan],
    primary_plans: list[ScenarioPlan],
    control_plans: list[ScenarioPlan],
) -> dict[str, Any]:
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
    pd.DataFrame(asdict(row) for row in scenario.initiatives).to_csv(
        output / "initiative_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.resolution_decisions).to_csv(
        output / "two_sided_resolution_decisions.csv",
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
        "resolution_decisions": len(scenario.resolution_decisions),
        "structural_resolution_counts": dict(
            Counter(
                row.resolution_type
                for row in scenario.resolution_decisions
                if row.structural_resolution
            )
        ),
        "tradeable_resolution_counts": dict(
            Counter(
                row.resolution_type
                for row in scenario.resolution_decisions
                if row.control_scenario_id is not None
            )
        ),
        "flow_aligned_tradeable_resolutions": sum(
            1
            for row in scenario.resolution_decisions
            if row.control_scenario_id is not None and row.flow_aligned is True
        ),
        "invalid_branch_first_resolutions": sum(
            1
            for row in scenario.resolution_decisions
            if row.reason_code == "FIRST_RESOLUTION_BELONGED_TO_INVALIDATED_BRANCH"
        ),
        "same_event_resolution_invalidations": sum(
            1
            for row in scenario.resolution_decisions
            if row.same_event_invalidation
        ),
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
    )
    feature_detector = ImpactRegimeDetector()
    scenario = TwoSidedPullbackAuctionStateMachine()
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        scenario.on_feature(index=index, features=feature_detector.features)

    primary_plans = evaluation_plans(
        scenario.primary_plans,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    control_plans = evaluation_plans(
        scenario.close_only_control_plans,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    if len(primary_plans) > len(control_plans):
        raise RuntimeError("v33 primary cannot exceed close-only control count")
    selected_plans = (
        primary_plans
        if args.rule == "first-resolution-aligned-flow"
        else control_plans
    )

    # Both variants receive an identical official TradeTick stream selected
    # from the union of the precomputed primary/control plan windows.
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
            f"BTCUSDT-v33-{args.rule}-"
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
    diagnostics = write_candidate_evidence(
        output,
        scenario,
        selected_plans=selected_plans,
        primary_plans=primary_plans,
        control_plans=control_plans,
    )
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [row.to_dict() for row in calibrations]},
    )

    payload = {
        "candidate": "two-sided first-completed pullback auction resolver",
        "candidate_version": 33,
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
            "opposite-flow pullback whose close preserves outside value"
        ),
        "two_sided_resolution_contract": (
            "maintain continuation and reversal branches independently; kill a "
            "branch when its opposite pullback stop trades; first completed "
            "structural close belonging to a live branch wins"
        ),
        "continuation_resolution": (
            "completed close beyond frozen pullback extreme in initiative direction"
        ),
        "reversal_resolution": (
            "completed close through accepted boundary and opposite frozen "
            "pullback extreme"
        ),
        "primary_variable": (
            "winning resolution event aggressive-flow sign agrees with resolved side"
            if args.rule == "first-resolution-aligned-flow"
            else "single ablation removes only winning-event flow agreement"
        ),
        "entry_contract": (
            "market bracket on first official venue trade strictly after the "
            "completed winning resolution"
        ),
        "invalidation_contract": (
            "opposite frozen pullback extreme plus one 7bp side-cost buffer for "
            "the resolved direction"
        ),
        "target_contract": (
            "nearest active unconsumed completed-day/week level strictly beyond "
            "the pre-initiative structure edge in the resolved direction"
        ),
        "scenario_counts": dict(scenario.counts),
        "selected_plan_count": len(selected_plans),
        "primary_plan_count": len(primary_plans),
        "control_plan_count": len(control_plans),
        "selected_response_counts": dict(
            Counter(plan.response for plan in selected_plans)
        ),
        **diagnostics,
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
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "two_sided_pullback_auction_v33_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v33-two-sided-pullback",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v33-two-sided-pullback",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
