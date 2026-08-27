#!/usr/bin/env python3
"""Candidate ML-MN V4: adverse sweep and deep-retest reacceptance.

Two causal scenario families share one decision policy:

1. ADVERSE_EVENT_OVERLAP_RECLAIM
   A failed auction prints an adverse event body at least 18 bps deep at a
   meaningful OB/BPR overlap.  The event is treated as a liquidity sweep,
   and the first available structural response is traded.

2. DEEP_RETEST_ACTIVITY_REACCEPTANCE
   A failed auction reaches DEEP_RETEST while recent activity is at least its
   local baseline.  The first active reacceptance response is traded.

The decision never uses symbol identity or outcome fields.  At most one plan is
kept per causal episode, and one global pending-order/position slot routes all
four markets through a continuous account.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

import liquidity_control_v2 as base

POLICY = "ML_MN_ADVERSE_SWEEP_REACCEPTANCE_V4"
RISK = 0.03
MIN_R = 1.0
MAX_R = 1.500001
EVENT_BODY_BPS_MAX = -18.0
DEEP_ACTIVITY_RATIO_MIN = 1.0


def first_plan_per_episode(plans: pd.DataFrame) -> pd.DataFrame:
    """Freeze the first qualifying plan in each causal episode."""
    if plans.empty:
        return plans.copy()
    return (
        plans.sort_values(
            [
                "research_period",
                "episode_id",
                "order_time_ns",
                "scenario_priority",
                "planned_target_net_r",
                "action_id",
            ],
            ascending=[True, True, True, False, True, True],
            kind="mergesort",
        )
        .drop_duplicates(["research_period", "episode_id"], keep="first")
        .sort_values(
            ["order_time_ns", "scenario_priority", "planned_target_net_r", "action_id"],
            ascending=[True, False, True, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )


def select_plans(frame: pd.DataFrame) -> pd.DataFrame:
    """Create immutable plans from causal state available at order time."""
    states = base.choose_geometry(frame)
    target_r = base.n(states, "planned_target_net_r")
    eligible = target_r.between(MIN_R, MAX_R)
    failed = base.s(states, "family").eq("FAILED_AUCTION_REVERSAL")
    location = base.s(states, "location_kind")

    adverse_sweep = states[
        eligible
        & failed
        & location.str.contains("OB_OVERLAP|BPR", regex=True, na=False)
        & base.n(states, "event_body_bps_signed").le(EVENT_BODY_BPS_MAX)
    ].copy()
    adverse_sweep["scenario_family"] = "ADVERSE_EVENT_OVERLAP_RECLAIM"
    adverse_sweep["scenario_priority"] = 2

    deep_reacceptance = states[
        eligible
        & failed
        & base.s(states, "auction_phase").eq("DEEP_RETEST")
        & base.n(states, "sequence_block_3_activity_ratio").ge(DEEP_ACTIVITY_RATIO_MIN)
    ].copy()
    deep_reacceptance["scenario_family"] = "DEEP_RETEST_ACTIVITY_REACCEPTANCE"
    deep_reacceptance["scenario_priority"] = 1

    plans = pd.concat([adverse_sweep, deep_reacceptance], ignore_index=True, sort=False)
    return first_plan_per_episode(plans)


def metric_block(orders: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
    return base.metric_block(orders, trades)


def summarize(
    source: pd.DataFrame,
    plans: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
) -> dict[str, Any]:
    periods = sorted(
        source["research_period"].astype(str).unique(),
        key=lambda period: int(
            source.loc[
                source["research_period"].astype(str).eq(period), "order_time_ns"
            ].min()
        ),
    )
    symbols = sorted(source["symbol"].astype(str).unique())
    scenarios = sorted(plans["scenario_family"].astype(str).unique()) if len(plans) else []

    def subset(frame: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
        if frame.empty or column not in frame:
            return pd.DataFrame()
        return frame[frame[column].astype(str).eq(value)]

    return {
        "policy": POLICY,
        "decision_uses_symbol_identity": False,
        "decision_uses_outcome_fields": False,
        "first_qualifying_state_per_causal_episode": True,
        "mechanisms": {
            "ADVERSE_EVENT_OVERLAP_RECLAIM": {
                "family": "FAILED_AUCTION_REVERSAL",
                "meaningful_location": "location_kind contains OB_OVERLAP or BPR",
                "event_body_bps_signed_max": EVENT_BODY_BPS_MAX,
            },
            "DEEP_RETEST_ACTIVITY_REACCEPTANCE": {
                "family": "FAILED_AUCTION_REVERSAL",
                "auction_phase": "DEEP_RETEST",
                "sequence_block_3_activity_ratio_min": DEEP_ACTIVITY_RATIO_MIN,
            },
        },
        "account": {
            "one_global_pending_or_position_slot": True,
            "one_plan_per_causal_episode": True,
            "risk_fraction_of_current_nav": RISK,
            "scale_in_or_out": False,
            "minimum_planned_target_net_r": MIN_R,
            "maximum_realized_target_net_r": base.CAP_R,
        },
        "candidate_plans_before_account_arbitration": int(len(plans)),
        "overall_continuous_account": metric_block(orders, trades),
        "by_period": {
            period: metric_block(
                subset(orders, "research_period", period),
                subset(trades, "research_period", period),
            )
            for period in periods
        },
        "by_symbol": {
            symbol: metric_block(
                subset(orders, "symbol", symbol),
                subset(trades, "symbol", symbol),
            )
            for symbol in symbols
        },
        "by_scenario": {
            scenario: metric_block(
                subset(orders, "scenario_family", scenario),
                subset(trades, "scenario_family", scenario),
            )
            for scenario in scenarios
        },
    }


def run(root: Path, output: Path, period_bounds: Path | None = None) -> dict[str, Any]:
    source = base.apply_period_bounds(base.load_actions(root), period_bounds)
    plans = select_plans(source)
    orders, trades = base.route_account(plans)
    result = summarize(source, plans, orders, trades)

    output.mkdir(parents=True, exist_ok=True)
    plans.to_csv(output / "candidate_plans.csv", index=False)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--period-bounds", type=Path)
    args = parser.parse_args()
    run(args.root, args.output, args.period_bounds)


if __name__ == "__main__":
    main()
