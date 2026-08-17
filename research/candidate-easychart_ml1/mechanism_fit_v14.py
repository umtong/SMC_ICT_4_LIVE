#!/usr/bin/env python3
"""Family-routed policy with causal passive-order accounting.

Selected passive orders reserve the account even when they never fill, but an
unfilled order is not counted as a completed trade or a loss. NAV is compounded
through every selected decision, while win rate, planned R and position holding
statistics are computed only from filled trades.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_fit_v3 as fit
import mechanism_fit_v11 as routed
from mechanism_tape_passive_v14 import FEATURE_COLUMNS


def _filled(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "filled" not in frame.columns:
        return frame
    mask = pd.to_numeric(frame["filled"], errors="coerce").fillna(1.0).eq(1.0)
    return frame[mask]


def _metrics(
    decisions: pd.DataFrame,
    calendar_days: int | None = None,
) -> dict[str, Any]:
    days = fit._calendar_days(decisions) if calendar_days is None else int(calendar_days)
    if decisions.empty:
        return {
            "orders": 0,
            "unfilled_orders": 0,
            "trades": 0,
            "calendar_days": days,
            "trades_per_calendar_day": 0.0,
            "ending_nav": 1.0,
            "maximum_drawdown": 0.0,
        }
    all_realized = pd.to_numeric(decisions["realized_r"], errors="raise").to_numpy(float)
    all_factors = 1.0 + fit.RISK_FRACTION * all_realized
    if np.any(all_factors <= 0.0):
        raise RuntimeError("non-positive account factor")
    nav = np.cumprod(all_factors)
    peak = np.maximum.accumulate(np.concatenate(([1.0], nav)))[1:]
    drawdown = nav / peak - 1.0

    trades = _filled(decisions)
    unfilled_orders = int(len(decisions) - len(trades))
    if trades.empty:
        return {
            "orders": int(len(decisions)),
            "unfilled_orders": unfilled_orders,
            "passive_fill_rate": 0.0,
            "trades": 0,
            "calendar_days": days,
            "trades_per_calendar_day": 0.0,
            "ending_nav": float(nav[-1]),
            "total_log_growth": float(np.log(nav[-1])),
            "maximum_drawdown": float(np.min(drawdown)),
            "unique_clusters": int(decisions["cluster_id"].nunique()),
        }
    realized = pd.to_numeric(trades["realized_r"], errors="raise").to_numpy(float)
    positive = realized[realized > 0.0]
    negative = realized[realized < 0.0]
    gross_profit = float(positive.sum())
    gross_loss = float(-negative.sum())
    target_r = pd.to_numeric(trades["target_r"], errors="raise").to_numpy(float)
    holding_column = (
        "position_duration_minutes"
        if "position_duration_minutes" in trades.columns
        else "duration_minutes"
    )
    holding = pd.to_numeric(trades[holding_column], errors="raise").to_numpy(float)
    passive_orders = decisions["entry_style"].astype(str).eq("PASSIVE_FIRST_RETEST")
    passive_count = int(passive_orders.sum())
    passive_filled = int(
        (
            passive_orders
            & pd.to_numeric(decisions["filled"], errors="coerce").fillna(1).eq(1)
        ).sum()
    )
    return {
        "orders": int(len(decisions)),
        "unfilled_orders": unfilled_orders,
        "passive_orders": passive_count,
        "passive_filled": passive_filled,
        "passive_fill_rate": float(passive_filled / max(passive_count, 1)),
        "trades": int(len(trades)),
        "calendar_days": days,
        "trades_per_calendar_day": float(len(trades) / max(days, 1)),
        "positive_trade_rate": float(np.mean(realized > 0.0)),
        "target_first_rate": float(np.mean(trades["outcome"].eq("TARGET_FIRST"))),
        "stop_first_rate": float(np.mean(trades["outcome"].eq("STOP_FIRST"))),
        "timeout_rate": float(np.mean(trades["outcome"].eq("TIMEOUT"))),
        "fast_stop_rate": float(
            pd.to_numeric(trades["fast_stop"], errors="coerce").fillna(0.0).mean()
        ),
        "mean_realized_r": float(np.mean(realized)),
        "median_realized_r": float(np.median(realized)),
        "mean_planned_target_r": float(np.mean(target_r)),
        "median_planned_target_r": float(np.median(target_r)),
        "profit_factor_r": None if gross_loss <= 0.0 else gross_profit / gross_loss,
        "mean_holding_minutes": float(np.mean(holding)),
        "median_holding_minutes": float(np.median(holding)),
        "p90_holding_minutes": float(np.quantile(holding, 0.90)),
        "mean_account_occupancy_minutes": float(
            pd.to_numeric(decisions["duration_minutes"], errors="raise").mean()
        ),
        "ending_nav": float(nav[-1]),
        "total_log_growth": float(np.log(nav[-1])),
        "mean_log_growth_per_trade": float(
            np.log(all_factors).sum() / max(len(trades), 1)
        ),
        "maximum_drawdown": float(np.min(drawdown)),
        "longest_nonpositive_streak": int(fit._longest_losing_streak(realized)),
        "mean_decision_edge": float(
            pd.to_numeric(decisions["decision_edge"], errors="coerce").mean()
        ),
        "mean_model_disagreement": float(
            pd.to_numeric(
                decisions["outcome_model_disagreement"], errors="coerce"
            ).mean()
        ),
        "unique_clusters": int(decisions["cluster_id"].nunique()),
    }


def _loss_diagnostics(decisions: pd.DataFrame) -> pd.DataFrame:
    trades = _filled(decisions)
    if trades.empty:
        return pd.DataFrame()
    losses = trades[pd.to_numeric(trades["realized_r"], errors="raise") <= 0.0].copy()
    if losses.empty:
        return losses
    columns = [
        "period",
        "family",
        "source",
        "symbol",
        "entry_style",
        "outcome",
        "fast_stop",
    ]
    return (
        losses.groupby(columns, dropna=False)
        .agg(
            trades=("action_id", "count"),
            mean_r=("realized_r", "mean"),
            median_r=("realized_r", "median"),
            mean_planned_r=("target_r", "mean"),
            mean_hold=("position_duration_minutes", "mean"),
            mean_edge=("decision_edge", "mean"),
            mean_stop_probability=("pred_stop_first_median", "mean"),
        )
        .reset_index()
        .sort_values(["trades", "mean_r"], ascending=[False, True])
    )


def _rewrite_summary(output: Path) -> None:
    path = output / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["policy"]["name"] = "EXACT_TAPE_PASSIVE_MARKET_FAMILY_ROUTER_V14"
    summary["policy"]["entry_policy"] = (
        "CAUSAL_CHOICE_AMONG_MARKET_NOW_LATER_RETEST_AND_RESTING_FIRST_RETEST"
    )
    summary["policy"]["passive_fill"] = (
        "ONE_TICK_TRADE_THROUGH_MAKER_FILL_PENDING_SLOT_RESERVED"
    )
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    fit.FEATURE_COLUMNS = FEATURE_COLUMNS
    fit._fit_ensemble = routed._fit_ensemble
    fit._metrics = _metrics
    fit._loss_diagnostics = _loss_diagnostics
    args = parse_args()
    fit.run(args.root, args.output)
    _rewrite_summary(args.output)


if __name__ == "__main__":
    main()
