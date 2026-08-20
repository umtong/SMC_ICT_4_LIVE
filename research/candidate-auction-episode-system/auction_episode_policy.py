#!/usr/bin/env python3
"""One-account policy for complete liquidity-boundary auction episodes.

The policy does not vote OB/FVG/channel/fakeout features independently.  Those fields
describe one causal hypothesis: a liquidity boundary was interacted with, price showed
a directional response, a first-return entry remains available, the structural stop is
near, and the next opposing-liquidity route can pay for the risk.

At each completed episode state the model estimates fill, terminal resolution and
TARGET_FIRST probability from information available at that state.  The first state in
an episode whose conservative post-cost expected log growth is positive may arm.  All
four instruments and cash then compete for the single account.  Filled positions end
only at the declared stop or target; pending orders end only when the causal first-return
opportunity is invalidated, spent or passed.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, CatBoostRegressor

import sequential_commitment_router_clean as clean

RISK = 0.03
EPS = 1e-12
CATEGORICAL = [
    "family",
    "side",
    "entry_geometry",
    "setup_kind",
    "location_kind",
    "source_pool_kind",
    "route_kind",
    "auction_phase",
]
ABSOLUTE_OR_ID = {
    "symbol",
    "period",
    "action_id",
    "state_id",
    "episode_id",
    "entry",
    "stop",
    "target",
    "route_price",
    "arm_index",
    "departure_time_ns",
    "order_time_ns",
    "order_terminal_time_ns",
    "fill_time_ns",
    "resolution_time_ns",
    "terminal_ns",
}
LABEL_TOKENS = (
    "outcome",
    "fill_state",
    "filled",
    "resolved",
    "win",
    "net_r",
    "mfe",
    "mae",
    "holding",
    "entry_wait",
    "actual_",
    "terminal_minutes_label",
    "realized",
    "future_",
    "state_best",
    "known_actions",
    "label",
    "diagnostic_",
)


def load_actions(root: Path) -> pd.DataFrame:
    frame = clean.load_actions(root).copy()
    frame["resolved_after_fill_label"] = np.where(
        frame.filled,
        frame.resolved.astype(float),
        np.nan,
    )
    frame["terminal_minutes_label"] = (
        pd.to_numeric(frame.terminal_ns, errors="coerce")
        - pd.to_numeric(frame.order_time_ns, errors="coerce")
    ) / 60_000_000_000.0
    frame["terminal_minutes_label"] = frame.terminal_minutes_label.clip(lower=1.0)
    if "auction_phase" not in frame:
        frame["auction_phase"] = "UNKNOWN"
    return frame


def feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    categorical = [column for column in CATEGORICAL if column in frame]
    numeric: list[str] = []
    for column in frame.columns:
        low = column.lower()
        if column in ABSOLUTE_OR_ID or column in categorical:
            continue
        if any(token in low for token in LABEL_TOKENS):
            continue
        if low.endswith("_time_ns") or low.endswith("_index"):
            continue
        if not pd.api.types.is_numeric_dtype(frame[column]):
            continue
        if frame[column].notna().sum() < max(40, int(0.05 * len(frame))):
            continue
        if frame[column].nunique(dropna=True) <= 1:
            continue
        numeric.append(column)
    return numeric, categorical


def _matrix(
    train: pd.DataFrame,
    test: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, list[int]]:
    columns = numeric + categorical
    x_train = train[columns].copy()
    x_test = test[columns].copy()
    for column in categorical:
        x_train[column] = x_train[column].fillna("__NA__").astype(str)
        x_test[column] = x_test[column].fillna("__NA__").astype(str)
    return x_train, x_test, [x_train.columns.get_loc(column) for column in categorical]


def _weights(frame: pd.DataFrame) -> np.ndarray:
    # A state may expose several immutable entry/target plans.  It remains one market
    # decision, so duplicated plan rows must not dominate model fitting.
    return 1.0 / frame.groupby("state_id").state_id.transform("size").to_numpy(float)


def classifier(
    train: pd.DataFrame,
    test: pd.DataFrame,
    label: str,
    numeric: list[str],
    categorical: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    train = train[train[label].notna()].copy()
    if len(train) < 120 or train[label].nunique() < 2:
        probability = (float(train[label].sum()) + 4.0) / (len(train) + 8.0)
        return np.full(len(test), probability), np.zeros(len(test))
    x_train, x_test, cat = _matrix(train, test, numeric, categorical)
    predictions = []
    for seed in (41, 173):
        model = CatBoostClassifier(
            iterations=260,
            depth=6,
            learning_rate=0.045,
            l2_leaf_reg=14.0,
            random_strength=1.4,
            loss_function="Logloss",
            auto_class_weights="Balanced",
            verbose=False,
            allow_writing_files=False,
            random_seed=seed,
            thread_count=-1,
        )
        model.fit(
            x_train,
            train[label].astype(int),
            cat_features=cat,
            sample_weight=_weights(train),
        )
        predictions.append(model.predict_proba(x_test)[:, 1])
    matrix = np.vstack(predictions)
    return matrix.mean(axis=0), matrix.std(axis=0)


def duration_model(
    train: pd.DataFrame,
    test: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    train = train[train.terminal_minutes_label.notna()].copy()
    if len(train) < 120:
        value = float(train.terminal_minutes_label.median()) if len(train) else 60.0
        return np.full(len(test), value), np.zeros(len(test))
    x_train, x_test, cat = _matrix(train, test, numeric, categorical)
    target = np.log1p(train.terminal_minutes_label.astype(float).to_numpy())
    predictions = []
    for seed in (53, 181):
        model = CatBoostRegressor(
            iterations=220,
            depth=6,
            learning_rate=0.045,
            l2_leaf_reg=14.0,
            random_strength=1.2,
            loss_function="MAE",
            verbose=False,
            allow_writing_files=False,
            random_seed=seed,
            thread_count=-1,
        )
        model.fit(
            x_train,
            target,
            cat_features=cat,
            sample_weight=_weights(train),
        )
        predictions.append(np.expm1(model.predict(x_test)))
    matrix = np.vstack(predictions)
    return matrix.mean(axis=0), matrix.std(axis=0)


def score_fold(
    train: pd.DataFrame,
    test: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
) -> pd.DataFrame:
    fill_mean, fill_std = classifier(train, test, "filled", numeric, categorical)
    filled_train = train[train.filled].copy()
    resolve_mean, resolve_std = classifier(
        filled_train,
        test,
        "resolved_after_fill_label",
        numeric,
        categorical,
    )
    resolved_train = train[train.filled & train.resolved & train.net_r.notna()].copy()
    win_mean, win_std = classifier(resolved_train, test, "win", numeric, categorical)
    duration_mean, duration_std = duration_model(train, test, numeric, categorical)

    output = test.copy()
    output["p_fill_low"] = np.clip(fill_mean - 0.25 * fill_std, 0.01, 0.99)
    output["p_resolve_low"] = np.clip(resolve_mean - 0.25 * resolve_std, 0.01, 0.99)
    output["p_win_low"] = np.clip(win_mean - 0.35 * win_std, 0.01, 0.99)
    output["predicted_terminal_minutes_high"] = np.maximum(
        1.0,
        duration_mean + 0.30 * duration_std,
    )
    target_r = pd.to_numeric(
        output.planned_target_net_r,
        errors="coerce",
    ).clip(lower=0.0).fillna(0.0).to_numpy(float)
    win_log = np.log1p(RISK * target_r)
    loss_log = math.log(1.0 - RISK)
    output["expected_log_growth"] = (
        output.p_fill_low
        * output.p_resolve_low
        * (output.p_win_low * win_log + (1.0 - output.p_win_low) * loss_log)
    )
    output["expected_log_growth_per_hour"] = (
        output.expected_log_growth
        / (output.predicted_terminal_minutes_high / 60.0)
    )
    output["route_utilization"] = (
        pd.to_numeric(output.gross_rr, errors="coerce")
        / pd.to_numeric(output.route_rr, errors="coerce").replace(0.0, np.nan)
    ).replace([np.inf, -np.inf], np.nan)
    return output


def _state_best(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(
            [
                "period",
                "state_id",
                "expected_log_growth_per_hour",
                "expected_log_growth",
                "planned_target_net_r",
            ],
            ascending=[True, True, False, False, False],
        )
        .groupby(["period", "state_id"], as_index=False)
        .first()
    )


def route(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    states = _state_best(frame).sort_values(
        ["period", "episode_id", "order_time_ns", "expected_log_growth_per_hour"],
        ascending=[True, True, True, False],
    )
    eligible = states[
        (states.expected_log_growth > 0.0)
        & ~states.auction_phase.astype(str).eq("FAILED_REENTRY")
    ].copy()
    # A causal episode can arm once.  Waiting is allowed only while cash is still the
    # better action; the first positive complete plan is the decision.
    first_positive = (
        eligible.groupby(["period", "episode_id"], as_index=False)
        .first()
        .sort_values(
            ["period", "order_time_ns", "expected_log_growth_per_hour", "expected_log_growth"],
            ascending=[True, True, False, False],
        )
    )

    selected_rows: list[pd.Series] = []
    for _, period_frame in first_positive.groupby("period", sort=True):
        busy_until = -np.inf
        for timestamp, simultaneous in period_frame.groupby("order_time_ns", sort=True):
            if not np.isfinite(timestamp) or float(timestamp) < busy_until:
                continue
            row = simultaneous.sort_values(
                ["expected_log_growth_per_hour", "expected_log_growth", "planned_target_net_r"],
                ascending=[False, False, False],
            ).iloc[0]
            selected_rows.append(row)
            busy_until = max(float(timestamp), float(row.terminal_ns))

    orders = (
        pd.DataFrame(selected_rows).reset_index(drop=True)
        if selected_rows
        else first_positive.iloc[:0].copy()
    )
    trades = orders[orders.resolved & orders.net_r.notna()].copy().reset_index(drop=True)
    nav = peak = 1.0
    maximum_drawdown = 0.0
    for result in pd.to_numeric(trades.net_r, errors="coerce").dropna():
        nav *= max(EPS, 1.0 + RISK * float(result))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
    calendar_days = 7 * int(frame.period.nunique())
    summary = {
        "selected_orders": int(len(orders)),
        "closed_trades": int(len(trades)),
        "periods": int(frame.period.nunique()),
        "calendar_days": int(calendar_days),
        "trades_per_day": float(len(trades) / max(calendar_days, 1)),
        "target_first_rate": float(trades.win.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "mean_planned_gross_rr": float(trades.gross_rr.mean()) if len(trades) else None,
        "median_hold_minutes": float(trades.holding_minutes.median()) if len(trades) else None,
        "mean_hold_minutes": float(trades.holding_minutes.mean()) if len(trades) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "by_period": trades.groupby("period").agg(
            trades=("net_r", "size"),
            target_first_rate=("win", "mean"),
            mean_net_r=("net_r", "mean"),
        ).reset_index().to_dict("records") if len(trades) else [],
        "by_family": trades.groupby("family").agg(
            trades=("net_r", "size"),
            target_first_rate=("win", "mean"),
            mean_net_r=("net_r", "mean"),
        ).reset_index().to_dict("records") if len(trades) else [],
        "by_phase": trades.groupby("auction_phase").agg(
            trades=("net_r", "size"),
            target_first_rate=("win", "mean"),
            mean_net_r=("net_r", "mean"),
        ).reset_index().to_dict("records") if len(trades) else [],
    }
    return orders, trades, summary


def diagnostic_clinics(
    scored: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    keep = [
        column
        for column in [
            "period", "symbol", "episode_id", "state_id", "action_id", "family",
            "side", "auction_phase", "order_time_ns", "entry_geometry", "gross_rr",
            "planned_target_net_r", "route_rr", "route_utilization", "p_fill_low",
            "p_resolve_low", "p_win_low", "expected_log_growth",
            "expected_log_growth_per_hour", "fill_state", "outcome", "net_r",
            "holding_minutes", "auction_progress_r", "auction_retrace_fraction",
            "auction_outside_close_ratio", "auction_outside_volume_ratio",
            "auction_path_efficiency", "auction_effort_result",
        ]
        if column in scored
    ]
    decisions = orders[keep].copy() if len(orders) else scored.iloc[:0][keep].copy()
    losses = trades[pd.to_numeric(trades.net_r, errors="coerce") <= 0.0][keep].copy()
    selected_episodes = set(zip(orders.period.astype(str), orders.episode_id.astype(str)))
    resolved = scored[scored.resolved & scored.net_r.notna()].copy()
    oracle = (
        resolved.sort_values(["period", "episode_id", "net_r"], ascending=[True, True, False])
        .groupby(["period", "episode_id"], as_index=False)
        .first()
    )
    mask = [
        (str(period), str(episode)) not in selected_episodes and float(net_r) > 0.0
        for period, episode, net_r in zip(oracle.period, oracle.episode_id, oracle.net_r)
    ]
    no_trade = oracle.loc[mask, keep].copy().sort_values(
        ["period", "net_r"],
        ascending=[True, False],
    )
    return decisions, losses, no_trade


def run(
    development_root: Path,
    fresh_root: Path | None,
    output: Path,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    development = load_actions(development_root)
    numeric, categorical = feature_columns(development)
    scored_folds = []
    for period in sorted(development.period.unique()):
        train = development[development.period != period]
        test = development[development.period == period]
        scored_folds.append(score_fold(train, test, numeric, categorical))
    development_scored = pd.concat(scored_folds, ignore_index=True, sort=False)
    dev_orders, dev_trades, dev_summary = route(development_scored)
    dev_decisions, dev_losses, dev_no_trade = diagnostic_clinics(
        development_scored,
        dev_orders,
        dev_trades,
    )
    result: dict[str, Any] = {
        "policy": "UNIFIED_CAUSAL_AUCTION_EPISODE_FIRST_POSITIVE_COMPLETE_PLAN",
        "development_oof": dev_summary,
        "features": {"numeric": numeric, "categorical": categorical},
    }
    dev_orders.to_csv(output / "development_orders.csv", index=False)
    dev_trades.to_csv(output / "development_trades.csv", index=False)
    dev_decisions.to_csv(output / "development_decisions.csv", index=False)
    dev_losses.to_csv(output / "development_loss_clinic.csv", index=False)
    dev_no_trade.to_csv(output / "development_no_trade_clinic.csv", index=False)

    if fresh_root is not None:
        fresh = load_actions(fresh_root)
        fresh_scored = score_fold(development, fresh, numeric, categorical)
        orders, trades, summary = route(fresh_scored)
        decisions, losses, no_trade = diagnostic_clinics(fresh_scored, orders, trades)
        orders.to_csv(output / "fresh_orders.csv", index=False)
        trades.to_csv(output / "fresh_trades.csv", index=False)
        decisions.to_csv(output / "fresh_decisions.csv", index=False)
        losses.to_csv(output / "fresh_loss_clinic.csv", index=False)
        no_trade.to_csv(output / "fresh_no_trade_clinic.csv", index=False)
        result["fresh"] = summary

    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.development_root, args.fresh_root, args.output)


if __name__ == "__main__":
    main()
