#!/usr/bin/env python3
"""Probe whether frozen ML-k V3 depends on one fortunate threshold vector.

This is not a selector and does not alter the frozen policy.  Each public
mechanism threshold is displaced one at a time by broad multiplicative factors,
the same one-slot account is replayed, and the resulting performance envelope is
recorded.  The probe is meant to distinguish a coherent causal region from an
isolated threshold hit before spending fresh data on the candidate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import control_transfer_router_v3 as policy  # noqa: E402

FACTORS = (0.7, 0.8, 0.9, 1.1, 1.2, 1.3)
RISK_FRACTION = 0.03


def account_metrics(orders: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
    values = (
        pd.to_numeric(trades.get("net_r_num"), errors="coerce").fillna(-1.0)
        if not trades.empty
        else pd.Series(dtype=float)
    )
    nav = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in values:
        nav = max(0.0, nav * (1.0 + RISK_FRACTION * float(value)))
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / peak if peak else 1.0)
    outcomes = (
        orders.get("outcome", pd.Series(dtype=str)).astype(str)
        if not orders.empty
        else pd.Series(dtype=str)
    )
    return {
        "orders": int(len(orders)),
        "unfilled_orders": int(outcomes.eq("UNFILLED").sum()),
        "closed_trades": int(len(trades)),
        "wins": int(values.gt(0).sum()),
        "win_rate": float(values.gt(0).mean()) if len(values) else 0.0,
        "sum_net_r": float(values.sum()) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "ending_nav": float(nav),
        "max_drawdown": float(max_drawdown),
    }


def ordered_periods(actions: pd.DataFrame) -> list[str]:
    return sorted(
        actions["research_period"].astype(str).unique(),
        key=lambda period: int(
            actions.loc[
                actions["research_period"].astype(str).eq(period), "order_time_ns"
            ].min()
        ),
    )


def evaluate(actions: pd.DataFrame, periods: list[str], thresholds: dict[str, float]) -> dict[str, Any]:
    original = policy.THRESHOLDS
    policy.THRESHOLDS = thresholds
    try:
        plans = policy.choose_public_plans(actions)
        orders, trades = policy.route_account(plans)
    finally:
        policy.THRESHOLDS = original

    result = account_metrics(orders, trades)
    period_net_r: dict[str, float] = {}
    for period in periods:
        if orders.empty:
            period_orders = orders
        else:
            period_orders = orders[
                orders["research_period"].astype(str).eq(period)
            ]
        if trades.empty:
            period_trades = trades
        else:
            period_trades = trades[
                trades["research_period"].astype(str).eq(period)
            ]
        period_net_r[period] = account_metrics(period_orders, period_trades)["sum_net_r"]
    result["period_net_r"] = period_net_r
    result["minimum_period_r"] = min(period_net_r.values()) if period_net_r else 0.0
    result["positive_periods"] = sum(value > 0.0 for value in period_net_r.values())
    return result


def run(root: Path, output: Path) -> dict[str, Any]:
    actions = policy.load_actions(root)
    periods = ordered_periods(actions)
    baseline_thresholds = dict(policy.THRESHOLDS)

    rows: list[dict[str, Any]] = []
    baseline = evaluate(actions, periods, baseline_thresholds)
    rows.append(
        {
            "threshold": "BASELINE",
            "factor": 1.0,
            "value": None,
            **baseline,
        }
    )

    for name, value in baseline_thresholds.items():
        if float(value) == 0.0:
            continue
        for factor in FACTORS:
            displaced = dict(baseline_thresholds)
            displaced[name] = float(value) * factor
            rows.append(
                {
                    "threshold": name,
                    "factor": factor,
                    "value": displaced[name],
                    **evaluate(actions, periods, displaced),
                }
            )

    frame = pd.DataFrame(rows)
    variants = frame[~frame["threshold"].eq("BASELINE")].copy()
    by_threshold: dict[str, dict[str, Any]] = {}
    for name, group in variants.groupby("threshold", sort=True):
        by_threshold[str(name)] = {
            "minimum_closed_trades": int(group["closed_trades"].min()),
            "minimum_sum_net_r": float(group["sum_net_r"].min()),
            "minimum_ending_nav": float(group["ending_nav"].min()),
            "maximum_drawdown": float(group["max_drawdown"].max()),
            "minimum_period_r": float(group["minimum_period_r"].min()),
            "minimum_positive_periods": int(group["positive_periods"].min()),
        }

    summary = {
        "policy": "ML_K_CAUSAL_CONTROL_TRANSFER_V3",
        "selection_or_optimization": False,
        "purpose": (
            "mechanism stability under one-at-a-time broad threshold displacement"
        ),
        "data_periods": periods,
        "baseline": baseline,
        "perturbation_factors": list(FACTORS),
        "thresholds_perturbed": int(variants["threshold"].nunique()),
        "variants": int(len(variants)),
        "envelope": {
            "minimum_closed_trades": int(variants["closed_trades"].min()),
            "maximum_closed_trades": int(variants["closed_trades"].max()),
            "minimum_sum_net_r": float(variants["sum_net_r"].min()),
            "maximum_sum_net_r": float(variants["sum_net_r"].max()),
            "minimum_ending_nav": float(variants["ending_nav"].min()),
            "maximum_drawdown": float(variants["max_drawdown"].max()),
            "minimum_period_r": float(variants["minimum_period_r"].min()),
            "minimum_positive_periods": int(variants["positive_periods"].min()),
        },
        "all_variants_positive_in_all_periods": bool(
            variants["positive_periods"].eq(len(periods)).all()
        ),
        "all_variants_at_least_40_trades": bool(
            variants["closed_trades"].ge(40).all()
        ),
        "by_threshold": by_threshold,
    }

    output.mkdir(parents=True, exist_ok=True)
    export = frame.copy()
    export["period_net_r"] = export["period_net_r"].map(
        lambda value: json.dumps(value, sort_keys=True)
    )
    export.to_csv(output / "threshold_displacements.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.output)


if __name__ == "__main__":
    main()
