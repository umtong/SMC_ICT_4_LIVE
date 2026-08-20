#!/usr/bin/env python3
"""Causal sequential first-return policy with account-time economics.

The policy estimates fill, eventual TP/SL resolution, target-first probability and
order-to-terminal time.  It arms only when conservative expected log growth per hour
exceeds the estimated value of waiting for a later completed state in the same causal
episode.  No time exit is introduced: duration is an opportunity-cost estimate used
before entry, while a filled position still exits only at its declared TP or SL.
"""
from __future__ import annotations

import argparse, json, math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, CatBoostRegressor

import sequential_commitment_router_clean as clean

base = clean.core
RISK = 0.03
EPS = 1e-12


def load_actions(root: Path) -> pd.DataFrame:
    frame = clean.load_actions(root)
    frame["terminal_minutes_label"] = (
        pd.to_numeric(frame.terminal_ns, errors="coerce")
        - pd.to_numeric(frame.order_time_ns, errors="coerce")
    ) / 60_000_000_000.0
    frame["terminal_minutes_label"] = frame.terminal_minutes_label.clip(lower=1.0)
    frame["resolved_after_fill_label"] = np.where(
        frame.filled, frame.resolved.astype(float), np.nan
    )
    return add_rate_labels(frame)


def add_rate_labels(frame: pd.DataFrame) -> pd.DataFrame:
    known = frame.realized_log.notna() & frame.terminal_minutes_label.notna()
    frame["realized_log_rate_label"] = np.where(
        known,
        frame.realized_log.astype(float) / (frame.terminal_minutes_label.astype(float) / 60.0),
        np.nan,
    )
    state = frame.groupby(
        ["period", "episode_id", "state_id", "order_time_ns"], as_index=False
    ).agg(
        state_best_rate_label=("realized_log_rate_label", "max"),
        state_best_log_label=("realized_log", "max"),
    ).sort_values(["period", "episode_id", "order_time_ns", "state_id"])
    state["future_best_rate_label"] = np.nan
    for _, index in state.groupby(["period", "episode_id"], sort=False).groups.items():
        positions = list(index)
        values = state.loc[positions, "state_best_rate_label"].to_numpy(float)
        future = np.full(len(values), np.nan)
        running = np.nan
        for pos in range(len(values) - 1, -1, -1):
            future[pos] = running
            value = values[pos]
            if np.isfinite(value):
                running = value if not np.isfinite(running) else max(running, value)
        state.loc[positions, "future_best_rate_label"] = future
    state["event_positive_label"] = state.state_best_log_label.gt(0.0).astype(float)
    state["future_positive_rate_label"] = state.future_best_rate_label.clip(lower=0.0).fillna(0.0)
    return frame.merge(
        state,
        on=["period", "episode_id", "state_id", "order_time_ns"],
        how="left",
    )


def feature_columns(frame: pd.DataFrame, *, state_only: bool = False):
    numeric, categorical = clean.feature_columns(frame, state_only=state_only)
    labels = {
        "terminal_minutes_label", "resolved_after_fill_label",
        "realized_log_rate_label", "state_best_rate_label",
        "state_best_log_label", "future_best_rate_label",
        "event_positive_label", "future_positive_rate_label",
    }
    return [column for column in numeric if column not in labels and not column.endswith("_label")], categorical


def _matrix(train, test, numeric, categorical):
    columns = numeric + categorical
    xtr, xte = train[columns].copy(), test[columns].copy()
    for column in categorical:
        xtr[column] = xtr[column].fillna("__NA__").astype(str)
        xte[column] = xte[column].fillna("__NA__").astype(str)
    return xtr, xte, [xtr.columns.get_loc(column) for column in categorical]


def classifier(train, test, label, numeric, categorical):
    train = train[train[label].notna()].copy()
    if len(train) < 100 or train[label].nunique() < 2:
        probability = (float(train[label].sum()) + 5.0) / (len(train) + 10.0)
        return np.full(len(test), probability), np.zeros(len(test))
    xtr, xte, cat = _matrix(train, test, numeric, categorical)
    weights = 1.0 / train.groupby("state_id").state_id.transform("size").to_numpy(float)
    predictions = []
    for seed in (37, 149):
        model = CatBoostClassifier(
            iterations=240, depth=6, learning_rate=0.045, l2_leaf_reg=12.0,
            random_strength=1.5, loss_function="Logloss", auto_class_weights="Balanced",
            verbose=False, allow_writing_files=False, random_seed=seed, thread_count=-1,
        )
        model.fit(xtr, train[label].astype(int), cat_features=cat, sample_weight=weights)
        predictions.append(model.predict_proba(xte)[:, 1])
    matrix = np.vstack(predictions)
    return matrix.mean(axis=0), matrix.std(axis=0)


def regressor(train, test, label, numeric, categorical, *, log_target=False):
    train = train[train[label].notna()].copy()
    if len(train) < 100:
        value = float(train[label].median()) if len(train) else 0.0
        return np.full(len(test), value), np.zeros(len(test))
    xtr, xte, cat = _matrix(train, test, numeric, categorical)
    target = train[label].astype(float).to_numpy()
    if log_target:
        target = np.log1p(np.maximum(target, 0.0))
    weights = 1.0 / train.groupby("state_id").state_id.transform("size").to_numpy(float)
    predictions = []
    for seed in (43, 157):
        model = CatBoostRegressor(
            iterations=230, depth=6, learning_rate=0.045, l2_leaf_reg=12.0,
            random_strength=1.3, loss_function="MAE", verbose=False,
            allow_writing_files=False, random_seed=seed, thread_count=-1,
        )
        model.fit(xtr, target, cat_features=cat, sample_weight=weights)
        prediction = model.predict(xte)
        if log_target:
            prediction = np.expm1(prediction)
        predictions.append(prediction)
    matrix = np.vstack(predictions)
    return matrix.mean(axis=0), matrix.std(axis=0)


def score_fold(train, test, plan_numeric, plan_cat, state_numeric, state_cat):
    fill_mean, fill_std = classifier(train, test, "filled", plan_numeric, plan_cat)
    filled_train = train[train.filled].copy()
    resolve_mean, resolve_std = classifier(
        filled_train, test, "resolved_after_fill_label", plan_numeric, plan_cat
    )
    resolved_train = train[train.filled & train.resolved & train.net_r.notna()].copy()
    win_mean, win_std = classifier(resolved_train, test, "win", plan_numeric, plan_cat)
    duration_mean, duration_std = regressor(
        train, test, "terminal_minutes_label", plan_numeric, plan_cat, log_target=True
    )
    state_train = train.sort_values("action_id").groupby("state_id", as_index=False).first()
    state_test = test.sort_values("action_id").groupby("state_id", as_index=False).first()
    good_mean, good_std = classifier(
        state_train, state_test, "event_positive_label", state_numeric, state_cat
    )
    wait_mean, wait_std = regressor(
        state_train, state_test, "future_positive_rate_label", state_numeric, state_cat
    )
    state_probability = state_test[["state_id"]].copy()
    state_probability["p_event_good_low"] = np.clip(good_mean - 0.35 * good_std, 0.01, 0.99)
    state_probability["wait_rate_high"] = np.maximum(0.0, wait_mean + 0.30 * wait_std)
    output = test.copy().merge(state_probability, on="state_id", how="left")
    output["p_fill_low"] = np.clip(fill_mean - 0.30 * fill_std, 0.01, 0.99)
    output["p_resolve_low"] = np.clip(resolve_mean - 0.30 * resolve_std, 0.01, 0.99)
    output["p_win_low"] = np.clip(win_mean - 0.40 * win_std, 0.01, 0.99)
    output["predicted_terminal_minutes_high"] = np.maximum(
        1.0, duration_mean + 0.35 * duration_std
    )
    target = pd.to_numeric(output.planned_target_net_r, errors="coerce").clip(lower=0.0).to_numpy(float)
    log_win = np.log1p(RISK * target)
    log_loss = math.log(1.0 - RISK)
    output["expected_arm_log"] = (
        output.p_fill_low * output.p_resolve_low
        * (output.p_win_low * log_win + (1.0 - output.p_win_low) * log_loss)
    )
    output["expected_arm_rate"] = (
        output.expected_arm_log
        / (output.predicted_terminal_minutes_high / 60.0)
        * output.p_event_good_low
    )
    output["stopping_advantage"] = output.expected_arm_rate - output.wait_rate_high
    return output


def route(frame):
    best = frame.sort_values(
        ["period", "state_id", "expected_arm_rate", "expected_arm_log", "planned_target_net_r"],
        ascending=[True, True, False, False, False],
    ).groupby(["period", "state_id"], as_index=False).first()
    best = best[
        (best.expected_arm_log > 0.0)
        & (best.expected_arm_rate > 0.0)
        & (best.stopping_advantage > 0.0)
    ].sort_values(
        ["period", "order_time_ns", "stopping_advantage", "expected_arm_rate", "state_id"],
        ascending=[True, True, False, False, True],
    )
    selected = []
    for period, group in best.groupby("period", sort=True):
        busy_until = -np.inf
        used = set()
        for timestamp, simultaneous in group.groupby("order_time_ns", sort=True):
            if not np.isfinite(timestamp) or timestamp < busy_until:
                continue
            available = simultaneous[~simultaneous.episode_id.astype(str).isin(used)]
            if available.empty:
                continue
            row = available.iloc[0]
            selected.append(row)
            used.add(str(row.episode_id))
            busy_until = max(float(timestamp), float(row.terminal_ns))
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else best.iloc[:0].copy()
    trades = orders[orders.resolved & orders.net_r.notna()].copy().reset_index(drop=True)
    nav = peak = 1.0
    maximum_drawdown = 0.0
    for result in trades.net_r.astype(float):
        nav *= max(EPS, 1.0 + RISK * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
    days = 7 * int(frame.period.nunique())
    summary = {
        "selected_orders": int(len(orders)), "closed_trades": int(len(trades)),
        "periods": int(frame.period.nunique()), "calendar_days": int(days),
        "trades_per_day": float(len(trades) / max(days, 1)),
        "target_first_rate": float(trades.win.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "mean_planned_gross_rr": float(trades.gross_rr.mean()) if len(trades) else None,
        "median_hold_minutes": float(trades.holding_minutes.median()) if len(trades) else None,
        "mean_hold_minutes": float(trades.holding_minutes.mean()) if len(trades) else None,
        "mean_order_terminal_minutes": float(trades.terminal_minutes_label.mean()) if len(trades) else None,
        "ending_nav_multiplier": float(nav), "maximum_drawdown": float(maximum_drawdown),
        "by_period": trades.groupby("period").agg(
            trades=("net_r", "size"), target_first_rate=("win", "mean"),
            mean_net_r=("net_r", "mean"),
        ).reset_index().to_dict("records") if len(trades) else [],
        "by_family": trades.groupby("family").agg(
            trades=("net_r", "size"), target_first_rate=("win", "mean"),
            mean_net_r=("net_r", "mean"),
        ).reset_index().to_dict("records") if len(trades) else [],
    }
    return orders, trades, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    development = load_actions(args.development_root)
    plan_numeric, plan_cat = feature_columns(development)
    state_numeric, state_cat = feature_columns(development, state_only=True)
    scored = []
    for period in sorted(development.period.unique()):
        train = development[development.period != period]
        test = development[development.period == period]
        scored.append(score_fold(train, test, plan_numeric, plan_cat, state_numeric, state_cat))
    oof = pd.concat(scored, ignore_index=True, sort=False)
    oof_orders, oof_trades, oof_summary = route(oof)
    oof.to_csv(args.output / "development_oof_plans.csv.gz", index=False, compression="gzip")
    oof_orders.to_csv(args.output / "development_oof_orders.csv", index=False)
    oof_trades.to_csv(args.output / "development_oof_trades.csv", index=False)
    result: dict[str, Any] = {
        "policy": "CAUSAL_FIRST_RETURN_LOG_GROWTH_PER_ACCOUNT_TIME_OPTIMAL_STOPPING",
        "development_oof": oof_summary,
        "features": {
            "plan_numeric": plan_numeric, "plan_categorical": plan_cat,
            "state_numeric": state_numeric, "state_categorical": state_cat,
        },
    }
    if args.fresh_root:
        fresh = load_actions(args.fresh_root)
        fresh_scored = score_fold(
            development, fresh, plan_numeric, plan_cat, state_numeric, state_cat
        )
        orders, trades, summary = route(fresh_scored)
        fresh_scored.to_csv(args.output / "fresh_scored_plans.csv.gz", index=False, compression="gzip")
        orders.to_csv(args.output / "fresh_orders.csv", index=False)
        trades.to_csv(args.output / "fresh_trades.csv", index=False)
        result["fresh"] = summary
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    (args.output / "RESULT.md").write_text(
        "# Account-time first-return result\n\n" + json.dumps(result, indent=2, default=str) + "\n"
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
