#!/usr/bin/env python3
"""Common-sweep acceptance policy for Candidate ML-MN V3.

The policy waits inside each causal liquidity episode until both conditions exist:
1) a market-wide event has moved at least 8 bps against the proposed trade;
2) the auction has subsequently accumulated acceptance strength of at least 2.2.

That sequence represents a cross-market liquidity sweep followed by acceptance/reclaim.
Only the first qualifying state is actionable.  Symbol identity and all outcome fields are
excluded from the decision.  The routed result is one continuous four-market account.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

import liquidity_control_v2 as base

RISK = 0.03
MIN_R = 1.0
MAX_SOURCE_R = 8.0
EVENT_COMMON_SWEEP = -0.0008
MIN_ACCEPTANCE_STRENGTH = 2.2
POLICY = "ML_MN_COMMON_SWEEP_ACCEPTANCE_V3"


def select_plans(frame: pd.DataFrame) -> pd.DataFrame:
    """Select the first accepted reclaim after a market-wide adverse sweep."""
    states = base.choose_geometry(frame)
    event_common = base.n(states, "event_common_return_1m_signed")
    acceptance = base.n(states, "auction_acceptance_strength")
    target_r = base.n(states, "planned_target_net_r")
    selected = states[
        target_r.between(MIN_R, MAX_SOURCE_R)
        & event_common.le(EVENT_COMMON_SWEEP)
        & acceptance.ge(MIN_ACCEPTANCE_STRENGTH)
    ].copy()
    selected["scenario_family"] = "COMMON_SWEEP_ACCEPTANCE_RECLAIM"
    selected["scenario_priority"] = 1
    selected = (
        selected.sort_values(
            [
                "research_period",
                "episode_id",
                "order_time_ns",
                "planned_target_net_r",
                "action_id",
            ],
            ascending=[True, True, True, True, True],
            kind="mergesort",
        )
        .drop_duplicates(["research_period", "episode_id"], keep="first")
        .sort_values(
            ["order_time_ns", "planned_target_net_r", "action_id"],
            ascending=[True, True, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )
    return selected


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
    return {
        "policy": POLICY,
        "decision_uses_symbol_identity": False,
        "decision_uses_outcome_fields": False,
        "first_qualifying_state_only": True,
        "mechanism": {
            "event_common_return_1m_signed_max": EVENT_COMMON_SWEEP,
            "auction_acceptance_strength_min": MIN_ACCEPTANCE_STRENGTH,
            "interpretation": "market-wide adverse liquidity sweep followed by accepted reclaim",
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
        "overall_continuous_account": base.metric_block(orders, trades),
        "by_period": {
            period: base.metric_block(
                orders[
                    orders["research_period"].astype(str).eq(period)
                ] if len(orders) and "research_period" in orders else pd.DataFrame(),
                trades[
                    trades["research_period"].astype(str).eq(period)
                ] if len(trades) and "research_period" in trades else pd.DataFrame(),
            )
            for period in periods
        },
        "by_symbol": {
            symbol: base.metric_block(
                orders[
                    orders["symbol"].astype(str).eq(symbol)
                ] if len(orders) and "symbol" in orders else pd.DataFrame(),
                trades[
                    trades["symbol"].astype(str).eq(symbol)
                ] if len(trades) and "symbol" in trades else pd.DataFrame(),
            )
            for symbol in symbols
        },
    }


def run(
    root: Path,
    output: Path,
    period_bounds: Path | None = None,
) -> dict[str, Any]:
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
