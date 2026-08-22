#!/usr/bin/env python3
"""Conservative one-account policy for candidate 1k exact-route auction episodes.

The model estimates causal fill, resolution and target-first probabilities, but the
trading decision is not a generic classifier threshold.  At every event-time state it
compares calibrated post-cost log growth with cash.  An episode can arm only after a
completed directional response/first-retest phase, and it can arm once.  Simultaneous
BTC/ETH/SOL/XRP opportunities compete for the one global account slot.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

import auction_episode_policy as base

RISK = 0.03
EPS = 1e-12
ALLOWED_PHASES = {
    "ACCEPTED_EXPANSION",
    "FIRST_RETEST_FORMING",
    "DEEP_RETEST",
}


def classifier(
    train: pd.DataFrame,
    test: pd.DataFrame,
    label: str,
    numeric: list[str],
    categorical: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Unbalanced-probability ensemble with explicit prior shrinkage.

    ``auto_class_weights=Balanced`` is useful for classification recall but changes the
    probability scale.  Expected-growth routing needs probabilities, so candidate 1k
    trains on the observed distribution and shrinks estimates toward a Beta prior.
    """
    train = train[train[label].notna()].copy()
    positives = float(pd.to_numeric(train[label], errors="coerce").fillna(0.0).sum())
    prior = (positives + 6.0) / (len(train) + 12.0) if len(train) else 0.5
    if len(train) < 140 or train[label].nunique() < 2:
        return np.full(len(test), prior), np.zeros(len(test))

    x_train, x_test, cat = base._matrix(train, test, numeric, categorical)
    predictions = []
    for seed in (41, 173, 389):
        model = CatBoostClassifier(
            iterations=300,
            depth=6,
            learning_rate=0.04,
            l2_leaf_reg=18.0,
            random_strength=1.2,
            loss_function="Logloss",
            verbose=False,
            allow_writing_files=False,
            random_seed=seed,
            thread_count=-1,
        )
        model.fit(
            x_train,
            train[label].astype(int),
            cat_features=cat,
            sample_weight=base._weights(train),
        )
        predictions.append(model.predict_proba(x_test)[:, 1])
    matrix = np.vstack(predictions)
    mean = 0.84 * matrix.mean(axis=0) + 0.16 * prior
    # Model disagreement plus finite-sample prior uncertainty.
    prior_uncertainty = math.sqrt(prior * (1.0 - prior) / max(len(train) + 12.0, 1.0))
    std = np.sqrt(matrix.var(axis=0) + prior_uncertainty**2)
    return np.clip(mean, 0.005, 0.995), std


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
    duration_mean, duration_std = base.duration_model(train, test, numeric, categorical)

    output = test.copy()
    output["p_fill_low"] = np.clip(fill_mean - 0.45 * fill_std, 0.005, 0.995)
    output["p_resolve_low"] = np.clip(resolve_mean - 0.45 * resolve_std, 0.005, 0.995)
    output["p_win_low"] = np.clip(win_mean - 0.65 * win_std, 0.005, 0.995)
    output["predicted_terminal_minutes_high"] = np.maximum(
        1.0,
        duration_mean + 0.35 * duration_std,
    )

    target_r = pd.to_numeric(
        output.planned_target_net_r,
        errors="coerce",
    ).clip(lower=0.0).fillna(0.0).to_numpy(float)
    win_log = np.log1p(RISK * target_r)
    loss_log = math.log(1.0 - RISK)
    denominator = np.maximum(win_log - loss_log, EPS)
    break_even = np.clip(-loss_log / denominator, 0.0, 1.0)
    output["break_even_win_probability"] = break_even
    output["win_probability_edge"] = output.p_win_low.to_numpy(float) - break_even
    output["expected_log_growth"] = (
        output.p_fill_low
        * output.p_resolve_low
        * (output.p_win_low * win_log + (1.0 - output.p_win_low) * loss_log)
    )
    output["expected_log_growth_per_hour"] = (
        output.expected_log_growth
        / (output.predicted_terminal_minutes_high / 60.0)
    )
    output["route_utilization"] = 1.0
    return output


def _state_best(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(
            [
                "period",
                "state_id",
                "expected_log_growth",
                "win_probability_edge",
                "p_fill_low",
                "planned_target_net_r",
            ],
            ascending=[True, True, False, False, False, False],
        )
        .groupby(["period", "state_id"], as_index=False)
        .first()
    )


def route(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    states = _state_best(frame).sort_values(
        ["period", "episode_id", "order_time_ns", "expected_log_growth"],
        ascending=[True, True, True, False],
    )
    phase = states.auction_phase.astype(str)
    eligible = states[
        (states.expected_log_growth > 0.0)
        & (states.win_probability_edge > 0.0)
        & phase.isin(ALLOWED_PHASES)
        & ~phase.eq("FAILED_REENTRY")
    ].copy()

    # This is online-causal: the first state that beats cash after a completed phase
    # arms.  Later states in the same episode are never consulted once an order exists.
    first_positive = (
        eligible.groupby(["period", "episode_id"], as_index=False)
        .first()
        .sort_values(
            ["period", "order_time_ns", "expected_log_growth", "win_probability_edge"],
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
                [
                    "expected_log_growth",
                    "win_probability_edge",
                    "p_fill_low",
                    "planned_target_net_r",
                ],
                ascending=[False, False, False, False],
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


def run(development_root: Path, fresh_root: Path | None, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    development = base.load_actions(development_root)
    numeric, categorical = base.feature_columns(development)
    scored_folds = []
    for period in sorted(development.period.unique()):
        train = development[development.period != period]
        test = development[development.period == period]
        scored_folds.append(score_fold(train, test, numeric, categorical))
    development_scored = pd.concat(scored_folds, ignore_index=True, sort=False)
    dev_orders, dev_trades, dev_summary = route(development_scored)
    dev_decisions, dev_losses, dev_no_trade = base.diagnostic_clinics(
        development_scored,
        dev_orders,
        dev_trades,
    )
    result: dict[str, Any] = {
        "policy": "CANDIDATE_1K_EXACT_ROUTE_CALIBRATED_CAUSAL_EPISODE",
        "development_oof": dev_summary,
        "features": {"numeric": numeric, "categorical": categorical},
    }
    development_scored.to_csv(output / "development_scored.csv", index=False)
    dev_orders.to_csv(output / "development_orders.csv", index=False)
    dev_trades.to_csv(output / "development_trades.csv", index=False)
    dev_decisions.to_csv(output / "development_decisions.csv", index=False)
    dev_losses.to_csv(output / "development_loss_clinic.csv", index=False)
    dev_no_trade.to_csv(output / "development_no_trade_clinic.csv", index=False)

    if fresh_root is not None:
        fresh = base.load_actions(fresh_root)
        fresh_scored = score_fold(development, fresh, numeric, categorical)
        orders, trades, summary = route(fresh_scored)
        decisions, losses, no_trade = base.diagnostic_clinics(
            fresh_scored,
            orders,
            trades,
        )
        fresh_scored.to_csv(output / "fresh_scored.csv", index=False)
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
