#!/usr/bin/env python3
"""Resolved-impact state with causal external-liquidity target extension.

The initiative and three-notional response state machine are exactly candidate
v17.  Its measured target is treated as the minimum expected auction travel,
not necessarily the final liquidity objective.  At each completed resolution,
this candidate inspects only already-confirmed, still-unconsumed 40-bps
intrinsic pivots.  When a same-direction external pool exists at least as far as
the measured target, the nearest such pool replaces the measured target.
Otherwise the original target is retained.

This is a target-routing candidate, not a new entry filter.  ``base-measured``
is the frozen ablation.  Official Binance Vision aggregate trades are executed
one-for-one as NautilusTrader TradeTicks; NautilusTrader owns fills, brackets,
fees, margin, positions and NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
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
from directional_change_failed_sweep_week import (  # noqa: E402
    DIRECTIONAL_CHANGE_FRACTION,
    DirectionalChangeDetector,
    MAXIMUM_HOLD_NS,
)
from impact_regime_probe import ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from intrinsic_external_liquidity_v2_week import target_geometry  # noqa: E402
from intrinsic_external_liquidity_v3_router import (  # noqa: E402
    build_open_liquidity_snapshots,
    liquidity_event_key,
)
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import (  # noqa: E402
    CLOCK_MINUTES,
    CONTEXT_DAYS,
    atomic_json,
    build_plans,
    execution_trade_windows,
    load_execution,
)

RULES = ("external-extended", "base-measured")


def route_external_targets(
    *,
    plans: list[ScenarioPlan],
    features: list[Any],
    events: list[Any],
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[list[ScenarioPlan], list[dict[str, Any]]]:
    if not plans:
        return [], []
    snapshots = build_open_liquidity_snapshots(
        features=features,
        events=events,
        signal_indices=(plan.signal_bar_index for plan in plans),
    )
    routed: list[ScenarioPlan] = []
    decisions: list[dict[str, Any]] = []
    for plan in plans:
        index = int(plan.signal_bar_index)
        expected_entry = float(features[index].bar.close)
        base_target = float(plan.target_price)
        base_distance = abs(base_target - expected_entry)
        target_event_type = "DOWN" if plan.side.value == "LONG" else "UP"
        snapshot = snapshots[index]
        open_keys = (
            snapshot.high_keys if plan.side.value == "LONG" else snapshot.low_keys
        )
        candidates: list[tuple[float, float, int, float, float]] = []
        for event in events:
            if event.event_type != target_event_type:
                continue
            if int(event.confirmation_index) > index:
                continue
            if liquidity_event_key(event) not in open_keys:
                continue
            target = float(event.pivot_price)
            if plan.side.value == "LONG" and target <= expected_entry:
                continue
            if plan.side.value == "SHORT" and target >= expected_entry:
                continue
            distance = abs(target - expected_entry)
            if distance + 1e-12 < base_distance:
                continue
            _, planned_gain, price_fraction, net_rr = target_geometry(
                expected_entry=expected_entry,
                stop=float(plan.stop_price),
                target=target,
                cost=cost,
            )
            if (
                price_fraction < minimum_price_risk_fraction
                or planned_gain <= 0.0
                or net_rr < minimum_net_reward_risk
            ):
                continue
            candidates.append(
                (
                    distance,
                    target,
                    int(event.confirmation_index),
                    price_fraction,
                    net_rr,
                ),
            )

        if candidates:
            distance, target, confirmation_index, price_fraction, net_rr = min(
                candidates,
                key=lambda row: (row[0], row[2], row[1]),
            )
            selected = replace(
                plan,
                scenario_id=(
                    plan.scenario_id
                    + f":external-target:{confirmation_index}"
                ),
                target_price=target,
                reason_code=plan.reason_code + "_TO_OPEN_EXTERNAL_POOL",
            )
            reason = "OPEN_EXTERNAL_POOL_BEYOND_MEASURED_MINIMUM"
        else:
            selected = plan
            target = base_target
            confirmation_index = None
            distance = base_distance
            _, _, price_fraction, net_rr = target_geometry(
                expected_entry=expected_entry,
                stop=float(plan.stop_price),
                target=base_target,
                cost=cost,
            )
            reason = "NO_FARTHER_OPEN_POOL_RETAIN_MEASURED_TARGET"
        routed.append(selected)
        decisions.append(
            {
                "scenario_id": plan.scenario_id,
                "response": plan.response,
                "side": plan.side.value,
                "signal_bar_index": index,
                "signal_time_ns": int(plan.signal_time_ns),
                "expected_entry": expected_entry,
                "stop_price": float(plan.stop_price),
                "base_target_price": base_target,
                "base_target_distance": base_distance,
                "selected_target_price": target,
                "selected_target_distance": distance,
                "external_confirmation_index": confirmation_index,
                "price_risk_fraction": price_fraction,
                "net_reward_risk_at_signal": net_rr,
                "reason_code": reason,
            },
        )
    return routed, decisions


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
    detector, resolver, base_plans = build_plans(
        bars,
        start_ns=start_ns,
        end_ns=end_ns,
        rule="resolved-full",
    )

    dc = DirectionalChangeDetector(
        threshold_fraction=DIRECTIONAL_CHANGE_FRACTION,
    )
    for index in range(len(detector.features)):
        dc.on_feature(index=index, features=detector.features)

    if args.rule == "external-extended":
        plans, target_decisions = route_external_targets(
            plans=base_plans,
            features=detector.features,
            events=dc.events,
            cost=execution.all_in_cost_bps_per_side / 10_000.0,
            minimum_price_risk_fraction=execution.minimum_price_risk_fraction,
            minimum_net_reward_risk=execution.minimum_net_reward_risk,
        )
    else:
        plans = list(base_plans)
        target_decisions = [
            {
                "scenario_id": plan.scenario_id,
                "response": plan.response,
                "side": plan.side.value,
                "signal_bar_index": int(plan.signal_bar_index),
                "signal_time_ns": int(plan.signal_time_ns),
                "expected_entry": float(
                    detector.features[plan.signal_bar_index].bar.close,
                ),
                "stop_price": float(plan.stop_price),
                "base_target_price": float(plan.target_price),
                "base_target_distance": abs(
                    float(plan.target_price)
                    - float(detector.features[plan.signal_bar_index].bar.close)
                ),
                "selected_target_price": float(plan.target_price),
                "selected_target_distance": abs(
                    float(plan.target_price)
                    - float(detector.features[plan.signal_bar_index].bar.close)
                ),
                "external_confirmation_index": None,
                "price_risk_fraction": None,
                "net_reward_risk_at_signal": None,
                "reason_code": "FROZEN_BASE_MEASURED_TARGET_CONTROL",
            }
            for plan in plans
        ]

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
        output / "pulse_events.csv", index=False,
    )
    pd.DataFrame(asdict(row) for row in resolver.transitions).to_csv(
        output / "resolution_transitions.csv", index=False,
    )
    pd.DataFrame(asdict(plan) for plan in base_plans).to_csv(
        output / "base_resolved_plans.csv", index=False,
    )
    pd.DataFrame(asdict(plan) for plan in plans).to_csv(
        output / "routed_resolved_plans.csv", index=False,
    )
    pd.DataFrame(target_decisions).to_csv(
        output / "target_routing.csv", index=False,
    )
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [item.to_dict() for item in calibrations]},
    )

    payload = {
        "candidate": "resolved impact with external-liquidity target extension",
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
        "base_plan_count": len(base_plans),
        "selected_plan_count": len(plans),
        "selected_response_counts": dict(
            Counter(plan.response for plan in plans)
        ),
        "target_routing_counts": dict(
            Counter(row["reason_code"] for row in target_decisions)
        ),
        "directional_change_events": len(dc.events),
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
    atomic_json(
        output / "external_target_resolved_impact_v18_summary.json",
        payload,
    )
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
        default=ROOT / ".cache" / "candidate-01-v18-external-impact-target",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v18-external-impact-target",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
