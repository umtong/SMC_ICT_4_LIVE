#!/usr/bin/env python3
"""Learn when to arm and which first-return plan to take, then route one account.

This is a causal optimal-stopping approximation. At each completed arm state, the
policy compares conservative post-cost value of arming now with the estimated value
of waiting for a later state in the same auction episode. Future path is used only to
train those labels. Runtime inputs end at the current completed bar.
"""
from __future__ import annotations

import argparse, json, math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, CatBoostRegressor

RISK = 0.03
EPS = 1e-12
PLAN_CATEGORICAL = ["family", "side", "entry_geometry", "setup_kind", "location_kind", "source_pool_kind", "route_kind"]
STATE_CATEGORICAL = ["family", "side", "setup_kind", "location_kind", "source_pool_kind"]
DENY = ("outcome", "fill_state", "fill_index", "fill_time", "resolution", "order_terminal", "entry_wait", "holding", "net_r", "mfe_r", "mae_r", "actual_", "diagnostic_response", "diagnostic_first_return", "diagnostic_retest", "response_", "state_best", "future_best", "tradeable_label", "stop_now_label", "realized_log")
PLAN_ONLY = {"entry", "stop", "target", "gross_rr", "risk_bps", "route_price", "route_rr", "planned_target_net_r", "target_net_r", "arm_index"}


def period_name(directory: Path) -> str:
    for token in ("dev-", "cal-", "fresh-", "eval-", "holdout-"):
        at = directory.name.find(token)
        if at >= 0:
            return directory.name[at:]
    return directory.name


def load_actions(root: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(root.rglob("departure_actions.csv.gz")):
        frame = pd.read_csv(path, low_memory=False)
        frame["period"] = period_name(path.parent)
        frames.append(frame)
    if not frames:
        raise RuntimeError(f"no sequential commitment actions below {root}")
    frame = pd.concat(frames, ignore_index=True, sort=False)
    for column in ("order_time_ns", "departure_time_ns", "order_terminal_time_ns", "fill_time_ns", "resolution_time_ns", "gross_rr", "planned_target_net_r", "actual_target_net_r", "net_r", "holding_minutes"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["filled"] = frame.fill_state.astype(str).str.startswith("FILLED")
    frame["resolved"] = frame.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE"])
    frame["win"] = frame.outcome.astype(str).eq("TARGET_FIRST")
    ambiguous = frame.outcome.astype(str).str.startswith("AMBIGUOUS")
    frame.loc[ambiguous & frame.net_r.isna(), "net_r"] = -1.0
    frame["target_net_r"] = frame.actual_target_net_r.where(frame.actual_target_net_r.notna(), frame.planned_target_net_r)
    frame["terminal_ns"] = pd.to_numeric(frame.order_terminal_time_ns, errors="coerce")
    frame["realized_log"] = np.where(frame.resolved & frame.net_r.notna(), np.log1p(np.clip(RISK * frame.net_r.astype(float), -0.99, None)), np.where(~frame.filled, 0.0, np.nan))
    return add_stopping_labels(frame)


def add_stopping_labels(frame: pd.DataFrame) -> pd.DataFrame:
    state = frame.groupby(["period", "episode_id", "state_id", "order_time_ns"], as_index=False).agg(state_best_log=("realized_log", "max"), known_actions=("realized_log", lambda x: int(x.notna().sum()))).sort_values(["period", "episode_id", "order_time_ns", "state_id"])
    state["future_best_log"] = np.nan
    for _, index in state.groupby(["period", "episode_id"], sort=False).groups.items():
        idx = list(index); values = state.loc[idx, "state_best_log"].to_numpy(float); future = np.full(len(values), np.nan); running = np.nan
        for pos in range(len(values) - 1, -1, -1):
            future[pos] = running
            value = values[pos]
            if np.isfinite(value): running = value if not np.isfinite(running) else max(running, value)
        state.loc[idx, "future_best_log"] = future
    state["tradeable_label"] = state.state_best_log.gt(0.0).astype(float)
    state["future_positive_value"] = state.future_best_log.clip(lower=0.0).fillna(0.0)
    return frame.merge(state, on=["period", "episode_id", "state_id", "order_time_ns"], how="left")


def feature_columns(frame: pd.DataFrame, *, state_only: bool = False):
    categorical = [c for c in (STATE_CATEGORICAL if state_only else PLAN_CATEGORICAL) if c in frame]
    identifiers = {"period", "symbol", "action_id", "state_id", "episode_id", "order_time_ns", "departure_time_ns", "order_terminal_time_ns", "fill_time_ns", "resolution_time_ns", "terminal_ns"}
    numeric = []
    for column in frame.columns:
        low = column.lower()
        if column in identifiers or column in categorical or (state_only and column in PLAN_ONLY): continue
        if any(token in low for token in DENY) or low.startswith("diagnostic_") or low.endswith("_time_ns"): continue
        if not pd.api.types.is_numeric_dtype(frame[column]): continue
        if frame[column].notna().sum() < max(30, int(0.06 * len(frame))) or frame[column].nunique(dropna=True) <= 1: continue
        numeric.append(column)
    return numeric, categorical


def _matrix(train, test, numeric, categorical):
    columns = numeric + categorical; xtr, xte = train[columns].copy(), test[columns].copy()
    for column in categorical:
        xtr[column] = xtr[column].fillna("__NA__").astype(str); xte[column] = xte[column].fillna("__NA__").astype(str)
    return xtr, xte, [xtr.columns.get_loc(c) for c in categorical]


def classifier(train, test, label, numeric, categorical):
    train = train[train[label].notna()].copy()
    if len(train) < 100 or train[label].nunique() < 2:
        p = (float(train[label].sum()) + 5.0) / (len(train) + 10.0); return np.full(len(test), p), np.zeros(len(test))
    xtr, xte, cat = _matrix(train, test, numeric, categorical); predictions = []
    weights = 1.0 / train.groupby("state_id").state_id.transform("size").to_numpy(float)
    for seed in (19, 83, 211):
        model = CatBoostClassifier(iterations=300, depth=6, learning_rate=0.04, l2_leaf_reg=10.0, random_strength=1.4, loss_function="Logloss", auto_class_weights="Balanced", verbose=False, allow_writing_files=False, random_seed=seed, thread_count=-1)
        model.fit(xtr, train[label].astype(int), cat_features=cat, sample_weight=weights); predictions.append(model.predict_proba(xte)[:, 1])
    matrix = np.vstack(predictions); return matrix.mean(axis=0), matrix.std(axis=0)


def regressor(train, test, label, numeric, categorical):
    train = train[train[label].notna()].copy()
    if len(train) < 100: return np.full(len(test), float(train[label].mean()) if len(train) else 0.0), np.zeros(len(test))
    xtr, xte, cat = _matrix(train, test, numeric, categorical); predictions = []
    weights = 1.0 / train.groupby("state_id").state_id.transform("size").to_numpy(float)
    for seed in (29, 97, 223):
        model = CatBoostRegressor(iterations=280, depth=6, learning_rate=0.04, l2_leaf_reg=10.0, random_strength=1.2, loss_function="MAE", verbose=False, allow_writing_files=False, random_seed=seed, thread_count=-1)
        model.fit(xtr, train[label].astype(float), cat_features=cat, sample_weight=weights); predictions.append(model.predict(xte))
    matrix = np.vstack(predictions); return matrix.mean(axis=0), matrix.std(axis=0)


def score_fold(train, test, plan_numeric, plan_cat, state_numeric, state_cat):
    fill_mean, fill_std = classifier(train, test, "filled", plan_numeric, plan_cat)
    resolved = train[train.filled & train.resolved & train.net_r.notna()].copy()
    win_mean, win_std = classifier(resolved, test, "win", plan_numeric, plan_cat)
    state_train = train.sort_values("action_id").groupby("state_id", as_index=False).first(); state_test = test.sort_values("action_id").groupby("state_id", as_index=False).first()
    good_mean, good_std = classifier(state_train, state_test, "tradeable_label", state_numeric, state_cat)
    wait_mean, wait_std = regressor(state_train, state_test, "future_positive_value", state_numeric, state_cat)
    sp = state_test[["state_id"]].copy(); sp["p_event_good_low"] = np.clip(good_mean - 0.35 * good_std, 0.01, 0.99); sp["wait_value_high"] = np.maximum(0.0, wait_mean + 0.25 * wait_std)
    output = test.copy().merge(sp, on="state_id", how="left")
    output["p_fill_low"] = np.clip(fill_mean - 0.30 * fill_std, 0.01, 0.99); output["p_win_low"] = np.clip(win_mean - 0.40 * win_std, 0.01, 0.99)
    target = output.target_net_r.clip(lower=0.0).to_numpy(float); log_win, log_loss = np.log1p(RISK * target), math.log(1.0 - RISK)
    output["expected_arm_log"] = output.p_fill_low * (output.p_win_low * log_win + (1.0 - output.p_win_low) * log_loss)
    output["arm_value_low"] = output.expected_arm_log * output.p_event_good_low; output["stopping_advantage"] = output.arm_value_low - output.wait_value_high
    return output


def route(frame):
    best = frame.sort_values(["period", "state_id", "arm_value_low", "target_net_r"], ascending=[True, True, False, False]).groupby(["period", "state_id"], as_index=False).first()
    best = best[(best.expected_arm_log > 0.0) & (best.stopping_advantage > 0.0)].sort_values(["period", "order_time_ns", "stopping_advantage", "arm_value_low", "state_id"], ascending=[True, True, False, False, True])
    selected = []
    for period, group in best.groupby("period", sort=True):
        busy_until = -np.inf; used = set()
        for timestamp, simultaneous in group.groupby("order_time_ns", sort=True):
            if not np.isfinite(timestamp) or timestamp < busy_until: continue
            available = simultaneous[~simultaneous.episode_id.astype(str).isin(used)]
            if available.empty: continue
            row = available.iloc[0]; selected.append(row); used.add(str(row.episode_id)); busy_until = max(float(timestamp), float(row.terminal_ns))
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else best.iloc[:0].copy(); trades = orders[orders.resolved & orders.net_r.notna()].copy().reset_index(drop=True)
    nav = peak = 1.0; mdd = 0.0
    for result in trades.net_r.astype(float): nav *= max(EPS, 1.0 + RISK * result); peak = max(peak, nav); mdd = max(mdd, 1.0 - nav / peak)
    days = 7 * int(frame.period.nunique())
    summary = {"selected_orders":int(len(orders)),"closed_trades":int(len(trades)),"periods":int(frame.period.nunique()),"calendar_days":int(days),"trades_per_day":float(len(trades)/max(days,1)),"target_first_rate":float(trades.win.mean()) if len(trades) else None,"mean_net_r":float(trades.net_r.mean()) if len(trades) else None,"mean_planned_gross_rr":float(trades.gross_rr.mean()) if len(trades) else None,"median_hold_minutes":float(trades.holding_minutes.median()) if len(trades) else None,"mean_hold_minutes":float(trades.holding_minutes.mean()) if len(trades) else None,"ending_nav_multiplier":float(nav),"maximum_drawdown":float(mdd),"by_period":trades.groupby("period").agg(trades=("net_r","size"),target_first_rate=("win","mean"),mean_net_r=("net_r","mean")).reset_index().to_dict("records") if len(trades) else [],"by_family":trades.groupby("family").agg(trades=("net_r","size"),target_first_rate=("win","mean"),mean_net_r=("net_r","mean")).reset_index().to_dict("records") if len(trades) else []}
    return orders, trades, summary


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--development-root", type=Path, required=True); parser.add_argument("--fresh-root", type=Path); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    development = load_actions(args.development_root); plan_num, plan_cat = feature_columns(development); state_num, state_cat = feature_columns(development, state_only=True)
    oof = pd.concat([score_fold(development[development.period != period], development[development.period == period], plan_num, plan_cat, state_num, state_cat) for period in sorted(development.period.unique())], ignore_index=True, sort=False)
    oof_orders, oof_trades, oof_summary = route(oof); oof.to_csv(args.output/"development_oof_plans.csv.gz",index=False,compression="gzip"); oof_orders.to_csv(args.output/"development_oof_orders.csv",index=False); oof_trades.to_csv(args.output/"development_oof_trades.csv",index=False)
    result: dict[str,Any] = {"policy":"CAUSAL_SEQUENTIAL_COMMITMENT_FIRST_RETURN_OPTIMAL_STOPPING","development_oof":oof_summary,"features":{"plan_numeric":plan_num,"plan_categorical":plan_cat,"state_numeric":state_num,"state_categorical":state_cat}}
    if args.fresh_root:
        fresh = load_actions(args.fresh_root); scored = score_fold(development, fresh, plan_num, plan_cat, state_num, state_cat); orders, trades, summary = route(scored); scored.to_csv(args.output/"fresh_scored_plans.csv.gz",index=False,compression="gzip"); orders.to_csv(args.output/"fresh_orders.csv",index=False); trades.to_csv(args.output/"fresh_trades.csv",index=False); result["fresh"] = summary
    (args.output/"summary.json").write_text(json.dumps(result,indent=2,default=str)+"\n"); (args.output/"RESULT.md").write_text("# Sequential commitment first-return result\n\n"+json.dumps(result,indent=2,default=str)+"\n"); print(json.dumps(result,indent=2,default=str))

if __name__ == "__main__": main()
