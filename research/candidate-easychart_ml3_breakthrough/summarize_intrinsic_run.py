#!/usr/bin/env python3
"""Join intrinsic plans, first-passage answers and actual account trades.

The output is deliberately trade-level.  It exists to inspect whether the
auction state machine found the right event, chose a coherent invalidation and
used the nearest still-available objective—not to manufacture a pass/fail
score.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-output", type=Path, required=True)
    return parser.parse_args()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(output: Path) -> dict[str, Any]:
    events = _read_csv(output / "decision_events.csv")
    labels = _read_csv(output / "counterfactual_plans.csv")
    trades = _read_csv(output / "trade_audit.csv")
    metrics = _json(output / "metrics.json")

    if events.empty or "kind" not in events.columns:
        plans = pd.DataFrame()
        event_counts: dict[str, int] = {}
    else:
        event_counts = {
            str(key): int(value)
            for key, value in events["kind"].value_counts(dropna=False).items()
        }
        plans = events[events["kind"] == "plan"].copy()
        plans = plans.sort_values(
            [column for column in ("ts_ns", "symbol", "plan_id") if column in plans.columns],
            kind="mergesort",
        )

    if not plans.empty:
        keep = [
            column
            for column in (
                "plan_id",
                "causal_event_id",
                "ts_ns",
                "symbol",
                "family",
                "side",
                "scenario_path",
                "scale_name",
                "interaction_time_ns",
                "trigger_time_ns",
                "entry",
                "stop",
                "target",
                "gross_rr",
                "higher_timeframe_minutes",
                "higher_zone_id",
                "target_zone_id",
                "higher_strength_ratio",
                "lower_strength_ratio",
                "trigger_strength_ratio",
                "overlap_lower",
                "overlap_upper",
            )
            if column in plans.columns
        ]
        review = plans[keep].copy()
        if {"entry", "stop", "target", "side"}.issubset(review.columns):
            entry = pd.to_numeric(review["entry"], errors="coerce")
            stop = pd.to_numeric(review["stop"], errors="coerce")
            target = pd.to_numeric(review["target"], errors="coerce")
            risk = (entry - stop).abs()
            reward = (target - entry).abs()
            review["risk_bps"] = 10_000.0 * risk / entry.abs()
            review["reward_bps"] = 10_000.0 * reward / entry.abs()
        if {"interaction_time_ns", "trigger_time_ns"}.issubset(review.columns):
            review["interaction_to_trigger_minutes"] = (
                pd.to_numeric(review["trigger_time_ns"], errors="coerce")
                - pd.to_numeric(review["interaction_time_ns"], errors="coerce")
            ) / 60_000_000_000.0
    else:
        review = pd.DataFrame()

    if not labels.empty and not review.empty and "plan_id" in labels.columns:
        outcome_columns = [
            column
            for column in (
                "plan_id",
                "counterfactual_outcome",
                "counterfactual_resolution_time",
                "counterfactual_minutes_to_resolution",
                "counterfactual_net_r_conservative",
                "counterfactual_target_net_r",
                "counterfactual_stop_net_r",
                "post_cost_break_even_target_probability",
                "seq_prior_sigma_1m",
                "seq_prior_range_fraction_1m",
                "local_flow_delta_z",
                "local_range_z",
                "common_return_z",
                "residual_return_z",
            )
            if column in labels.columns
        ]
        review = review.merge(
            labels[outcome_columns],
            on="plan_id",
            how="left",
            validate="one_to_one",
        )

    if not trades.empty and not review.empty and "plan_id" in trades.columns:
        trade_columns = [
            column
            for column in (
                "plan_id",
                "ts_opened",
                "ts_closed",
                "duration_ns",
                "actual_entry",
                "actual_exit",
                "realized_pnl",
                "commissions",
                "actual_net_r",
                "exit_role",
                "risk_budget_utilization",
            )
            if column in trades.columns
        ]
        selected = trades[trade_columns].copy()
        selected = selected.drop_duplicates("plan_id", keep="last")
        review = review.merge(selected, on="plan_id", how="left", validate="one_to_one")

    review_path = output / "intrinsic_plan_review.csv"
    review.to_csv(review_path, index=False)

    if not review.empty and "counterfactual_outcome" in review.columns:
        stop_like = review[
            review["counterfactual_outcome"].isin(["STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"])
        ].copy()
        if "counterfactual_minutes_to_resolution" in stop_like.columns:
            stop_like = stop_like.sort_values(
                ["counterfactual_minutes_to_resolution", "ts_ns"],
                kind="mergesort",
            )
        stop_like.head(100).to_csv(output / "intrinsic_earliest_failures.csv", index=False)
        winners = review[review["counterfactual_outcome"] == "TARGET_FIRST"].copy()
        if "counterfactual_minutes_to_resolution" in winners.columns:
            winners = winners.sort_values(
                ["counterfactual_minutes_to_resolution", "ts_ns"],
                kind="mergesort",
            )
        winners.head(100).to_csv(output / "intrinsic_fastest_winners.csv", index=False)

    outcome_counts: dict[str, int] = {}
    by_family: dict[str, Any] = {}
    by_symbol: dict[str, Any] = {}
    if not review.empty and "counterfactual_outcome" in review.columns:
        outcome_counts = {
            str(key): int(value)
            for key, value in review["counterfactual_outcome"].fillna("<NA>").value_counts().items()
        }
        for field, destination in (("family", by_family), ("symbol", by_symbol)):
            if field not in review.columns:
                continue
            for key, group in review.groupby(field, dropna=False, sort=True):
                resolved = group[group["counterfactual_outcome"].isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"])]
                destination[str(key)] = {
                    "plans": int(len(group)),
                    "causal_events": int(group["causal_event_id"].nunique()) if "causal_event_id" in group else int(len(group)),
                    "target_first": int((group["counterfactual_outcome"] == "TARGET_FIRST").sum()),
                    "target_first_rate_resolved": None if resolved.empty else float((resolved["counterfactual_outcome"] == "TARGET_FIRST").mean()),
                    "median_gross_rr": None if "gross_rr" not in group else float(pd.to_numeric(group["gross_rr"], errors="coerce").median()),
                    "median_resolution_minutes": None if "counterfactual_minutes_to_resolution" not in group else float(pd.to_numeric(group["counterfactual_minutes_to_resolution"], errors="coerce").median()),
                    "sum_counterfactual_net_r": None if "counterfactual_net_r_conservative" not in group else float(pd.to_numeric(group["counterfactual_net_r_conservative"], errors="coerce").sum()),
                }

    summary = {
        "metrics": metrics,
        "event_counts": event_counts,
        "plans": int(len(review)),
        "causal_events": int(review["causal_event_id"].nunique()) if not review.empty and "causal_event_id" in review else int(len(review)),
        "submitted_trades": int(review["actual_net_r"].notna().sum()) if not review.empty and "actual_net_r" in review else 0,
        "outcomes": outcome_counts,
        "by_family": by_family,
        "by_symbol": by_symbol,
        "plan_review": str(review_path),
    }
    (output / "intrinsic_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    args = parse_args()
    print(json.dumps(summarize(args.run_output), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
