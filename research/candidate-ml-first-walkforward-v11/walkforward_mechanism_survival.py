#!/usr/bin/env python3
"""Quarterly walk-forward mechanism-survival account from one continuous event stream.

At each quarter boundary the mechanism-conditioned ordered-survival policy is
refit using only plans whose entry resolution was already known before that
boundary.  The fixed learning and routing algorithm then trades the next quarter.
Pending orders and filled positions carry across retraining boundaries, all four
markets share one account, and NAV compounds continuously at 3% risk.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def import_v9():
    path = Path(__file__).resolve().parents[1] / "candidate-ml-first-mechanism-survival-v9" / "mechanism_survival_policy.py"
    spec = importlib.util.spec_from_file_location("walkforward_v9_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V9 = import_v9()
V7 = V9.V7
CORE = V9.CORE
RISK = V9.RISK


def quarter_label(timestamp: pd.Series) -> pd.Series:
    naive = timestamp.dt.tz_convert("UTC").dt.tz_localize(None)
    return naive.dt.to_period("Q").astype(str)


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, tuple):
        return [safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def choose_policy(frame: pd.DataFrame, development_periods: list[str]):
    numeric, categorical = CORE.feature_columns(frame)
    predicted, _ = V9.fit_predictions(frame, numeric, categorical)
    predicted["_mechanism"] = V7.mechanism(predicted)
    calibrated, _, _ = V7.calibrated_predictions(predicted)
    calibrated["_planned_reward_r"] = V7.planned_reward(calibrated)
    components, _ = V9.exact_components(calibrated, development_periods)
    if not components:
        return calibrated, [], {"reason": "no development component"}

    policies = {"best_mechanism_component": [components[0]]}
    fused = V9.greedy_exact_union(calibrated, components, development_periods)
    if fused:
        policies["mechanism_survival_fusion"] = fused
    failed = [component for component in components if component.scope.startswith("failed")]
    accepted = [component for component in components if component.scope.startswith("accepted")]
    if failed:
        policies["failed_auction_survival"] = [failed[0]]
    if accepted:
        policies["accepted_auction_survival"] = [accepted[0]]

    rows = []
    for name, policy in policies.items():
        _, stats = V7.simulate(calibrated[calibrated["_role"] == "dev"], policy, development_periods)
        rows.append((stats["robust_objective"], stats["completed_trades"], name, policy, stats))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, selected_name, selected_policy, selected_stats = rows[0]
    description = {
        "selected_variant": selected_name,
        "development": selected_stats,
        "components": [
            {
                "mechanism": component.scope,
                "maximum_route_fraction": component.max_fraction,
                "uncertainty_penalty": component.penalty,
                "minimum_expected_log_growth": component.threshold,
                "development_component_score": component.score,
            }
            for component in selected_policy
        ],
        "numeric_features": numeric,
        "categorical_features": categorical,
    }
    return calibrated, selected_policy, description


def execute_block(
    evaluation: pd.DataFrame,
    policy,
    busy_until: pd.Timestamp,
    locked_episodes: set[str],
):
    candidates = V7.candidate_union(evaluation, policy)
    candidates = candidates.sort_values(["_decision", "_policy_expected_log"], ascending=[True, False], kind="mergesort")
    candidates["_minute"] = candidates["_decision"].dt.floor("min")
    completed = []
    selected = []
    for _, simultaneous in candidates.groupby("_minute", sort=True):
        decision = simultaneous["_decision"].min()
        if decision < busy_until:
            continue
        simultaneous = simultaneous[~simultaneous["_episode"].isin(locked_episodes)]
        if simultaneous.empty:
            continue
        chosen = simultaneous.sort_values(
            ["_policy_expected_log", "_component_priority", "route_fraction"],
            ascending=[False, False, True],
            kind="mergesort",
        ).iloc[0]
        locked_episodes.add(str(chosen["_episode"]))
        selected.append(chosen)
        if bool(chosen["_filled"]):
            busy_until = max(chosen["_exit_ts"], chosen["_fill_ts"] + pd.Timedelta(minutes=1))
            if pd.notna(chosen["_net_r"]):
                completed.append(chosen)
        else:
            busy_until = max(chosen["_cancel_ts"], decision + pd.Timedelta(minutes=1))
    completed_frame = pd.DataFrame(completed) if completed else evaluation.iloc[0:0].copy()
    selected_frame = pd.DataFrame(selected) if selected else evaluation.iloc[0:0].copy()
    return completed_frame, selected_frame, busy_until, locked_episodes


def metrics(values: np.ndarray, timestamps: pd.Series, start: pd.Timestamp, end: pd.Timestamp, period_rows):
    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    gross_win = 0.0
    gross_loss = 0.0
    wins = 0
    for value in values:
        nav *= max(1e-12, 1.0 + RISK * float(value))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        if value > 0:
            gross_win += float(value)
            wins += 1
        elif value < 0:
            gross_loss += float(-value)
    days = max(1, int((end - start).total_seconds() // 86400))
    return {
        "completed_trades": int(len(values)),
        "calendar_days": int(days),
        "trades_per_day": float(len(values) / days),
        "win_rate": float(wins / len(values)) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "median_net_r": float(np.median(values)) if len(values) else 0.0,
        "profit_factor_r": float(gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else math.inf),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "positive_quarters": int(sum(row["nav_multiplier"] > 1.0 for row in period_rows)),
        "quarters": period_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--first-evaluation-quarter", default="2025Q2")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    raw = CORE.read_plans(args.root)
    frame, schema = CORE.normalize(raw)
    frame["_quarter"] = quarter_label(frame["_decision"])
    quarters = sorted(frame["_quarter"].dropna().unique())
    if args.first_evaluation_quarter not in quarters:
        raise SystemExit(f"first evaluation quarter {args.first_evaluation_quarter} absent; quarters={quarters}")
    first_index = quarters.index(args.first_evaluation_quarter)
    if first_index < 5:
        raise SystemExit("walk-forward requires at least five full prior quarters")

    busy_until = pd.Timestamp.min.tz_localize("UTC")
    locked_episodes: set[str] = set()
    completed_blocks = []
    selected_blocks = []
    block_rows = []
    model_rows = []

    for index in range(first_index, len(quarters)):
        evaluation_quarter = quarters[index]
        evaluation_start = pd.Period(evaluation_quarter, freq="Q").start_time.tz_localize("UTC")
        evaluation_end = pd.Period(evaluation_quarter, freq="Q").end_time.tz_localize("UTC") + pd.Timedelta(nanoseconds=1)
        available_development = quarters[:index]

        development = frame[frame["_quarter"].isin(available_development)].copy()
        resolved = np.where(
            development["_filled"],
            development["_exit_ts"] < evaluation_start,
            development["_cancel_ts"] < evaluation_start,
        )
        development = development[resolved].copy()
        evaluation = frame[frame["_quarter"] == evaluation_quarter].copy()
        if evaluation.empty:
            continue
        development["_period"] = development["_quarter"]
        evaluation["_period"] = evaluation["_quarter"]
        development["_role"] = "dev"
        evaluation["_role"] = "fresh"
        research_frame = pd.concat([development, evaluation], ignore_index=True, sort=False)
        development_periods = sorted(development["_period"].unique())

        scored, policy, description = choose_policy(research_frame, development_periods)
        scored_evaluation = scored[scored["_role"] == "fresh"].copy()
        before_values = np.concatenate([
            block["_net_r"].astype(float).to_numpy() for block in completed_blocks
        ]) if completed_blocks else np.array([], dtype=float)
        before_nav = float(np.prod(1.0 + RISK * before_values)) if len(before_values) else 1.0
        if policy:
            completed, selected, busy_until, locked_episodes = execute_block(
                scored_evaluation, policy, busy_until, locked_episodes
            )
        else:
            completed = scored_evaluation.iloc[0:0].copy()
            selected = scored_evaluation.iloc[0:0].copy()
        if not completed.empty:
            completed["_walkforward_quarter"] = evaluation_quarter
            completed_blocks.append(completed)
        if not selected.empty:
            selected["_walkforward_quarter"] = evaluation_quarter
            selected_blocks.append(selected)
        after_values = np.concatenate([
            block["_net_r"].astype(float).to_numpy() for block in completed_blocks
        ]) if completed_blocks else np.array([], dtype=float)
        after_nav = float(np.prod(1.0 + RISK * after_values)) if len(after_values) else 1.0
        block_rows.append(
            {
                "quarter": evaluation_quarter,
                "training_quarters": len(development_periods),
                "selected_plans": int(len(selected)),
                "completed_trades": int(len(completed)),
                "mean_net_r": float(completed["_net_r"].mean()) if len(completed) else 0.0,
                "nav_multiplier": float(after_nav / before_nav) if before_nav else 0.0,
                "continuous_nav": after_nav,
                "busy_until_after_block": str(busy_until),
            }
        )
        model_rows.append(
            {
                "quarter": evaluation_quarter,
                "training_quarters": development_periods,
                **description,
            }
        )

    completed_all = pd.concat(completed_blocks, ignore_index=True, sort=False) if completed_blocks else frame.iloc[0:0].copy()
    selected_all = pd.concat(selected_blocks, ignore_index=True, sort=False) if selected_blocks else frame.iloc[0:0].copy()
    start = pd.Period(args.first_evaluation_quarter, freq="Q").start_time.tz_localize("UTC")
    end = frame["_decision"].max()
    values = completed_all["_net_r"].astype(float).to_numpy() if len(completed_all) else np.array([], dtype=float)
    continuous = metrics(values, completed_all["_decision"] if len(completed_all) else pd.Series(dtype="datetime64[ns, UTC]"), start, end, block_rows)

    summary = {
        "policy": "ML_FIRST_QUARTERLY_WALKFORWARD_MECHANISM_SURVIVAL_V11",
        "first_evaluation_quarter": args.first_evaluation_quarter,
        "available_quarters": quarters,
        "continuous_account": continuous,
        "models_by_quarter": model_rows,
        "schema": schema,
        "walkforward_contract": {
            "training_uses_only_resolved_prior_quarters": True,
            "evaluation_predictions_are_fixed_before_each_quarter": True,
            "pending_orders_and_positions_carry_across_quarters": True,
            "one_global_pending_or_position": True,
            "one_selection_per_causal_episode": True,
            "risk_fraction": RISK,
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        },
    }

    columns = [column for column in completed_all.columns if not str(column).startswith("_")]
    columns += [
        "_walkforward_quarter", "_decision", "_fill_ts", "_exit_ts", "_net_r",
        "_raw_fill", "_raw_reach", "_fill_calibrated", "_reach_calibrated",
        "_policy_expected_log", "_planned_reward_r", "_mechanism", "_component",
    ]
    columns = list(dict.fromkeys(column for column in columns if column in completed_all.columns))
    completed_all[columns].to_csv(args.output / "completed_trades.csv", index=False)
    selected_columns = list(dict.fromkeys(column for column in columns if column in selected_all.columns))
    selected_all[selected_columns].to_csv(args.output / "selected_plans.csv", index=False)
    pd.DataFrame(block_rows).to_csv(args.output / "quarter_metrics.csv", index=False)
    (args.output / "models_by_quarter.json").write_text(json.dumps(safe(model_rows), indent=2, sort_keys=True) + "\n")
    (args.output / "summary.json").write_text(json.dumps(safe(summary), indent=2, sort_keys=True) + "\n")
    print(json.dumps(safe(summary), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
