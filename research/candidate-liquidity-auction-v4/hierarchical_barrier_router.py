#!/usr/bin/env python3
"""Learn event opportunity and plan geometry under a TP/SL-only exit contract.

Censored plans are not counted as losses and are never liquidated by a vertical barrier.
The global router may select such a plan, in which case that account remains occupied to
the end of available data.  Only TARGET_FIRST and STOP_FIRST complete a trade and NAV.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler

warnings.filterwarnings("ignore")
RISK_FRACTION = 0.03
EPS = 1e-12

LABEL_TOKENS = (
    "outcome",
    "net_r",
    "resolved_label",
    "win_label",
    "best_plan_label",
    "event_trade_label",
    "event_best_r",
    "event_mean_r",
    "event_std_r",
    "relative_r",
    "exit_time",
    "hold_minutes",
    "mfe",
    "mae",
    "actual_",
    "future",
    "resolution",
    "label",
    "nav_",
)
IDENTITY = {
    "period",
    "symbol",
    "event_id",
    "action_id",
    "event_time",
    "entry_time",
    "entry",
    "stop",
    "target",
}
CATEGORICAL_CANDIDATES = (
    "family",
    "side",
    "stop_kind",
    "route_kind",
)


def _read_universes(root: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    frames: list[pd.DataFrame] = []
    period_days: dict[str, int] = {}
    for path in sorted(root.glob("**/barrier_actions.csv.gz")):
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frames.append(frame)
        summary_path = path.parent / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            start = pd.Timestamp(summary["decision_start"])
            end = pd.Timestamp(summary["decision_end"])
            period_days[str(summary["period"])] = int((end - start).days + 1)
    if not frames:
        raise RuntimeError(f"no barrier action universes under {root}")
    actions = pd.concat(frames, ignore_index=True, sort=False)
    actions["entry_time"] = pd.to_datetime(actions["entry_time"], utc=True, errors="raise")
    actions["exit_time"] = pd.to_datetime(actions["exit_time"], utc=True, errors="raise")
    actions["net_r"] = pd.to_numeric(actions["net_r"], errors="coerce")
    actions["actual_target_net_r"] = pd.to_numeric(actions["actual_target_net_r"], errors="coerce")
    actions["resolved_label"] = actions["outcome"].isin(["TARGET_FIRST", "STOP_FIRST"]).astype(int)
    actions["win_label"] = np.where(
        actions["resolved_label"].eq(1),
        actions["outcome"].eq("TARGET_FIRST").astype(int),
        np.nan,
    )
    return actions, period_days


def _attach_event_labels(actions: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for (period, event_id), group in actions.groupby(["period", "event_id"], sort=False):
        resolved = group[group["resolved_label"].eq(1) & group["net_r"].notna()].copy()
        base = {
            "period": period,
            "event_id": event_id,
            "event_plan_count": int(len(group)),
            "event_resolved_count": int(len(resolved)),
            "event_censored_count": int(len(group) - len(resolved)),
            "event_family_count": int(group["family"].nunique()),
        }
        if resolved.empty:
            records.append(
                {
                    **base,
                    "event_best_r": np.nan,
                    "event_mean_r": np.nan,
                    "event_std_r": np.nan,
                    "event_win_share": np.nan,
                    "event_trade_label": np.nan,
                    "best_action_id": None,
                }
            )
            continue
        best = resolved.sort_values(
            ["net_r", "win_label", "actual_target_net_r"],
            ascending=[False, False, False],
        ).iloc[0]
        records.append(
            {
                **base,
                "event_best_r": float(resolved["net_r"].max()),
                "event_mean_r": float(resolved["net_r"].mean()),
                "event_std_r": float(resolved["net_r"].std(ddof=0)),
                "event_win_share": float(resolved["win_label"].mean()),
                "event_trade_label": int(float(best["net_r"]) > 0.0),
                "best_action_id": str(best["action_id"]) if float(best["net_r"]) > 0.0 else None,
            }
        )
    event = pd.DataFrame(records)
    output = actions.merge(event, on=["period", "event_id"], how="left")
    output["best_plan_label"] = np.where(
        output["event_trade_label"].notna(),
        (output["action_id"].astype(str) == output["best_action_id"].astype(str)).astype(int),
        np.nan,
    )
    output["relative_r"] = output["net_r"] - output["event_mean_r"]
    return output


def _feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str], list[str], list[str]]:
    categories = sorted([column for column in CATEGORICAL_CANDIDATES if column in frame.columns])
    numeric: list[str] = []
    for column in frame.columns:
        lower = str(column).lower()
        if column in IDENTITY or column in categories:
            continue
        if any(token in lower for token in LABEL_TOKENS):
            continue
        if column in {"risk_bps", "gross_rr"}:
            # These are plan geometry and belong in the plan model only.
            continue
        if pd.api.types.is_numeric_dtype(frame[column]) and frame[column].notna().mean() > 0.20 and frame[column].nunique(dropna=True) > 1:
            numeric.append(column)
    event_extra = [
        "event_plan_count",
        "event_resolved_count",
        "event_censored_count",
        "event_family_count",
    ]
    event_numeric = sorted(set(numeric + event_extra))
    plan_numeric = sorted(set(numeric + event_extra + ["risk_bps", "gross_rr", "actual_target_net_r"]))
    event_categories = [column for column in ("family",) if column in categories]
    return event_numeric, event_categories, plan_numeric, categories


def _preprocessor(numeric: list[str], categories: list[str]) -> ColumnTransformer:
    return ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", RobustScaler(quantile_range=(10.0, 90.0))),
                    ]
                ),
                numeric,
            ),
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5)),
                    ]
                ),
                categories,
            ),
        ],
        sparse_threshold=0.0,
    )


def _event_rows(frame: pd.DataFrame) -> pd.DataFrame:
    # A causal state row is chosen without looking at outcomes: smallest risk route first,
    # then deterministic action identity. Event aggregates carry plan-set breadth.
    return (
        frame.sort_values(
            ["period", "event_id", "gross_rr", "risk_bps", "action_id"],
            ascending=[True, True, True, True, True],
        )
        .groupby(["period", "event_id"], as_index=False)
        .first()
    )


def _fit_models(
    train: pd.DataFrame,
    event_numeric: list[str],
    event_categories: list[str],
    plan_numeric: list[str],
    plan_categories: list[str],
) -> dict[str, Any]:
    resolved = train[
        train["resolved_label"].eq(1)
        & train["net_r"].notna()
        & train["event_trade_label"].notna()
        & train["best_plan_label"].notna()
    ].copy()
    if len(resolved) < 2_000:
        raise RuntimeError(f"insufficient resolved training actions: {len(resolved)}")
    event = _event_rows(resolved)

    event_prep = _preprocessor(event_numeric, event_categories)
    ze = event_prep.fit_transform(event[event_numeric + event_categories])
    opportunity_tree = ExtraTreesClassifier(
        n_estimators=220,
        min_samples_leaf=18,
        max_features=0.58,
        class_weight="balanced",
        n_jobs=-1,
        random_state=41001,
    )
    opportunity_hist = HistGradientBoostingClassifier(
        learning_rate=0.04,
        max_iter=120,
        max_leaf_nodes=17,
        min_samples_leaf=30,
        l2_regularization=5.0,
        random_state=41003,
    )
    event_r_tree = ExtraTreesRegressor(
        n_estimators=220,
        min_samples_leaf=18,
        max_features=0.58,
        n_jobs=-1,
        random_state=41005,
    )
    event_r_hist = HistGradientBoostingRegressor(
        learning_rate=0.04,
        max_iter=120,
        max_leaf_nodes=17,
        min_samples_leaf=30,
        l2_regularization=5.0,
        loss="huber",
        random_state=41007,
    )
    event_q25 = HistGradientBoostingRegressor(
        learning_rate=0.04,
        max_iter=120,
        max_leaf_nodes=17,
        min_samples_leaf=30,
        l2_regularization=6.0,
        loss="quantile",
        quantile=0.25,
        random_state=41009,
    )
    opportunity_tree.fit(ze, event["event_trade_label"].astype(int))
    opportunity_hist.fit(ze, event["event_trade_label"].astype(int))
    event_r_tree.fit(ze, event["event_best_r"].astype(float))
    event_r_hist.fit(ze, event["event_best_r"].astype(float))
    event_q25.fit(ze, event["event_best_r"].astype(float))

    plan_prep = _preprocessor(plan_numeric, plan_categories)
    zp = plan_prep.fit_transform(resolved[plan_numeric + plan_categories])
    best_tree = ExtraTreesClassifier(
        n_estimators=240,
        min_samples_leaf=14,
        max_features=0.58,
        class_weight="balanced",
        n_jobs=-1,
        random_state=41011,
    )
    best_hist = HistGradientBoostingClassifier(
        learning_rate=0.04,
        max_iter=120,
        max_leaf_nodes=19,
        min_samples_leaf=26,
        l2_regularization=5.0,
        random_state=41013,
    )
    win_tree = ExtraTreesClassifier(
        n_estimators=240,
        min_samples_leaf=14,
        max_features=0.58,
        class_weight="balanced",
        n_jobs=-1,
        random_state=41015,
    )
    win_hist = HistGradientBoostingClassifier(
        learning_rate=0.04,
        max_iter=120,
        max_leaf_nodes=19,
        min_samples_leaf=26,
        l2_regularization=5.0,
        random_state=41017,
    )
    r_tree = ExtraTreesRegressor(
        n_estimators=240,
        min_samples_leaf=14,
        max_features=0.58,
        n_jobs=-1,
        random_state=41019,
    )
    r_hist = HistGradientBoostingRegressor(
        learning_rate=0.04,
        max_iter=120,
        max_leaf_nodes=19,
        min_samples_leaf=26,
        l2_regularization=5.0,
        loss="huber",
        random_state=41021,
    )
    r_q25 = HistGradientBoostingRegressor(
        learning_rate=0.04,
        max_iter=120,
        max_leaf_nodes=19,
        min_samples_leaf=26,
        l2_regularization=6.0,
        loss="quantile",
        quantile=0.25,
        random_state=41023,
    )
    duration_tree = ExtraTreesRegressor(
        n_estimators=200,
        min_samples_leaf=18,
        max_features=0.55,
        n_jobs=-1,
        random_state=41025,
    )
    duration_hist = HistGradientBoostingRegressor(
        learning_rate=0.04,
        max_iter=100,
        max_leaf_nodes=17,
        min_samples_leaf=30,
        l2_regularization=5.0,
        loss="huber",
        random_state=41027,
    )
    best_tree.fit(zp, resolved["best_plan_label"].astype(int))
    best_hist.fit(zp, resolved["best_plan_label"].astype(int))
    win_tree.fit(zp, resolved["win_label"].astype(int))
    win_hist.fit(zp, resolved["win_label"].astype(int))
    r_tree.fit(zp, resolved["net_r"].astype(float))
    r_hist.fit(zp, resolved["net_r"].astype(float))
    r_q25.fit(zp, resolved["net_r"].astype(float))
    log_duration = np.log1p(resolved["hold_minutes"].astype(float).clip(lower=1.0))
    duration_tree.fit(zp, log_duration)
    duration_hist.fit(zp, log_duration)
    return {
        "event_prep": event_prep,
        "opportunity_tree": opportunity_tree,
        "opportunity_hist": opportunity_hist,
        "event_r_tree": event_r_tree,
        "event_r_hist": event_r_hist,
        "event_q25": event_q25,
        "plan_prep": plan_prep,
        "best_tree": best_tree,
        "best_hist": best_hist,
        "win_tree": win_tree,
        "win_hist": win_hist,
        "r_tree": r_tree,
        "r_hist": r_hist,
        "r_q25": r_q25,
        "duration_tree": duration_tree,
        "duration_hist": duration_hist,
        "event_numeric": event_numeric,
        "event_categories": event_categories,
        "plan_numeric": plan_numeric,
        "plan_categories": plan_categories,
    }


def _predict(bundle: dict[str, Any], frame: pd.DataFrame) -> pd.DataFrame:
    event = _event_rows(frame)
    ze = bundle["event_prep"].transform(event[bundle["event_numeric"] + bundle["event_categories"]])
    event_p1 = bundle["opportunity_tree"].predict_proba(ze)[:, 1]
    event_p2 = bundle["opportunity_hist"].predict_proba(ze)[:, 1]
    event_r1 = bundle["event_r_tree"].predict(ze)
    event_r2 = bundle["event_r_hist"].predict(ze)
    event_map = event[["period", "event_id"]].copy()
    event_map["event_opportunity_p"] = 0.5 * (event_p1 + event_p2)
    event_map["event_predicted_best_r"] = 0.5 * (event_r1 + event_r2)
    event_map["event_q25_best_r"] = bundle["event_q25"].predict(ze)
    event_map["event_disagreement"] = 0.45 * np.abs(event_p1 - event_p2) + 0.20 * np.abs(event_r1 - event_r2)
    merged = frame.merge(event_map, on=["period", "event_id"], how="left", suffixes=("", "_prediction"))

    zp = bundle["plan_prep"].transform(merged[bundle["plan_numeric"] + bundle["plan_categories"]])
    best_p1 = bundle["best_tree"].predict_proba(zp)[:, 1]
    best_p2 = bundle["best_hist"].predict_proba(zp)[:, 1]
    win_p1 = bundle["win_tree"].predict_proba(zp)[:, 1]
    win_p2 = bundle["win_hist"].predict_proba(zp)[:, 1]
    r1 = bundle["r_tree"].predict(zp)
    r2 = bundle["r_hist"].predict(zp)
    duration1 = bundle["duration_tree"].predict(zp)
    duration2 = bundle["duration_hist"].predict(zp)

    best_p = 0.5 * (best_p1 + best_p2)
    win_p = 0.5 * (win_p1 + win_p2)
    predicted_r = 0.5 * (r1 + r2)
    q25_r = bundle["r_q25"].predict(zp)
    expected_minutes = np.expm1(0.5 * (duration1 + duration2)).clip(1.0, 10_080.0)
    target_r = merged["actual_target_net_r"].astype(float).to_numpy()
    expected_log = win_p * np.log1p(RISK_FRACTION * target_r) + (1.0 - win_p) * math.log1p(-RISK_FRACTION)
    predicted_log = np.log1p(np.clip(RISK_FRACTION * predicted_r, -0.99, None))
    q25_log = np.log1p(np.clip(RISK_FRACTION * q25_r, -0.99, None))
    disagreement = (
        merged["event_disagreement"].to_numpy(float)
        + 0.30 * np.abs(best_p1 - best_p2)
        + 0.30 * np.abs(win_p1 - win_p2)
        + 0.18 * np.abs(r1 - r2)
    )
    event_quality = 0.50 + 0.50 * merged["event_opportunity_p"].to_numpy(float)
    plan_quality = 0.50 + 0.50 * best_p
    economic = (0.62 * expected_log + 0.23 * predicted_log + 0.15 * q25_log) * event_quality * plan_quality
    score = economic / np.sqrt(expected_minutes / 60.0) - 0.0015 * disagreement
    output = merged.copy()
    output["event_opportunity_p"] = merged["event_opportunity_p"].to_numpy(float)
    output["event_predicted_best_r"] = merged["event_predicted_best_r"].to_numpy(float)
    output["event_q25_best_r"] = merged["event_q25_best_r"].to_numpy(float)
    output["plan_best_p"] = best_p
    output["plan_win_p"] = win_p
    output["plan_predicted_r"] = predicted_r
    output["plan_q25_r"] = q25_r
    output["expected_resolution_minutes"] = expected_minutes
    output["model_disagreement"] = disagreement
    output["expected_log_growth"] = expected_log
    output["policy_score"] = score
    return output


def _route_period(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates = frame[frame["policy_score"].gt(0.0)].copy()
    candidates = candidates.sort_values(
        ["entry_time", "policy_score", "event_opportunity_p", "plan_best_p", "plan_win_p", "action_id"],
        ascending=[True, False, False, False, False, True],
    )
    selected: list[pd.Series] = []
    used_events: set[str] = set()
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    for entry_time, group in candidates.groupby("entry_time", sort=True):
        timestamp = pd.Timestamp(entry_time)
        if timestamp < busy_until:
            continue
        available = group[~group["event_id"].astype(str).isin(used_events)]
        if available.empty:
            continue
        chosen = available.iloc[0]
        selected.append(chosen)
        used_events.add(str(chosen["event_id"]))
        busy_until = pd.Timestamp(chosen["exit_time"])
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else candidates.iloc[0:0].copy()
    trades = orders[
        orders["resolved_label"].eq(1)
        & orders["outcome"].isin(["TARGET_FIRST", "STOP_FIRST"])
        & orders["net_r"].notna()
    ].copy().reset_index(drop=True)
    return orders, trades


def _account_summary(
    scored: pd.DataFrame,
    period_days: dict[str, int],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    all_orders: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    by_period: dict[str, Any] = {}
    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for period, group in scored.groupby("period", sort=True):
        orders, trades = _route_period(group)
        all_orders.append(orders)
        all_trades.append(trades)
        start_nav = nav
        for result in trades["net_r"].astype(float):
            nav *= max(1e-9, 1.0 + RISK_FRACTION * result)
            peak = max(peak, nav)
            maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        by_period[str(period)] = {
            "decision_days": int(period_days.get(str(period), 0)),
            "selected_orders": int(len(orders)),
            "closed_trades": int(len(trades)),
            "open_positions_at_data_end": int(len(orders) - len(trades)),
            "target_first_rate": float(trades["outcome"].eq("TARGET_FIRST").mean()) if len(trades) else None,
            "mean_net_r": float(trades["net_r"].mean()) if len(trades) else None,
            "mean_hold_minutes": float(trades["hold_minutes"].mean()) if len(trades) else None,
            "nav_multiplier_from_closed_trades": float(nav / start_nav),
        }
    orders = pd.concat(all_orders, ignore_index=True, sort=False) if all_orders else scored.iloc[0:0].copy()
    trades = pd.concat(all_trades, ignore_index=True, sort=False) if all_trades else scored.iloc[0:0].copy()
    days = sum(period_days.get(str(period), 0) for period in scored["period"].unique())
    summary = {
        "periods": int(scored["period"].nunique()),
        "decision_days": int(days),
        "selected_orders": int(len(orders)),
        "closed_trades": int(len(trades)),
        "open_positions_at_data_end": int(len(orders) - len(trades)),
        "closed_trades_per_day": float(len(trades) / max(days, 1)),
        "target_first_rate": float(trades["outcome"].eq("TARGET_FIRST").mean()) if len(trades) else None,
        "mean_net_r": float(trades["net_r"].mean()) if len(trades) else None,
        "mean_hold_minutes": float(trades["hold_minutes"].mean()) if len(trades) else None,
        "median_hold_minutes": float(trades["hold_minutes"].median()) if len(trades) else None,
        "ending_nav_multiplier_from_closed_trades": float(nav),
        "maximum_drawdown_from_closed_trades": float(maximum_drawdown),
        "by_period": by_period,
    }
    return orders, trades, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    actions, period_days = _read_universes(args.root)
    actions = _attach_event_labels(actions)
    event_numeric, event_categories, plan_numeric, plan_categories = _feature_columns(actions)

    train_mask = actions["period"].astype(str).str.startswith("train-")
    calibration_mask = actions["period"].astype(str).str.startswith("cal-")
    holdout_mask = actions["period"].astype(str).str.startswith(("holdout-", "eval-"))
    if not train_mask.any():
        raise RuntimeError("no train-* periods")

    development_bundle = _fit_models(
        actions.loc[train_mask], event_numeric, event_categories, plan_numeric, plan_categories
    )
    calibration_scored = _predict(development_bundle, actions.loc[calibration_mask]) if calibration_mask.any() else actions.iloc[0:0].copy()
    calibration_orders, calibration_trades, calibration_summary = _account_summary(calibration_scored, period_days) if not calibration_scored.empty else (calibration_scored, calibration_scored, {})

    final_train_mask = train_mask | calibration_mask
    final_bundle = _fit_models(
        actions.loc[final_train_mask], event_numeric, event_categories, plan_numeric, plan_categories
    )
    holdout_scored = _predict(final_bundle, actions.loc[holdout_mask]) if holdout_mask.any() else actions.iloc[0:0].copy()
    holdout_orders, holdout_trades, holdout_summary = _account_summary(holdout_scored, period_days) if not holdout_scored.empty else (holdout_scored, holdout_scored, {})

    calibration_scored.to_csv(args.output / "calibration_scored_actions.csv.gz", index=False, compression="gzip")
    calibration_orders.to_csv(args.output / "calibration_account_orders.csv", index=False)
    calibration_trades.to_csv(args.output / "calibration_account_trades.csv", index=False)
    holdout_scored.to_csv(args.output / "holdout_scored_actions.csv.gz", index=False, compression="gzip")
    holdout_orders.to_csv(args.output / "holdout_account_orders.csv", index=False)
    holdout_trades.to_csv(args.output / "holdout_account_trades.csv", index=False)
    joblib.dump(final_bundle, args.output / "hierarchical_barrier_model.joblib", compress=3)

    summary = {
        "policy": "event opportunity -> direction/stop/route plan -> one global position; TP or SL are the only exits",
        "exit_contract": "TARGET_FIRST or STOP_FIRST only; CENSORED_OPEN remains open and never contributes realized R",
        "input_actions": int(len(actions)),
        "input_events": int(actions["event_id"].nunique()),
        "resolved_actions": int(actions["resolved_label"].sum()),
        "censored_actions": int(actions["outcome"].eq("CENSORED_OPEN").sum()),
        "features": {
            "event_numeric": event_numeric,
            "event_categorical": event_categories,
            "plan_numeric": plan_numeric,
            "plan_categorical": plan_categories,
        },
        "calibration": calibration_summary,
        "holdout": holdout_summary,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
