#!/usr/bin/env python3
"""Fit and freeze the V4 causal trigger on development periods only.

The fitting target is deliberately two-headed. A setup must repeatedly create
a reachable +1R excursion and have evidence that its declared natural target
can complete. Leave-one-period-out predictions choose one common threshold and
one common timing envelope. Symbol and calendar identity never enter features.
"""
from __future__ import annotations

import argparse
from datetime import date
import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import easychart_b_v4 as router


def _first_numeric(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    for name in names:
        if name in frame:
            return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(np.nan, index=frame.index, dtype=float)


def _labels(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    outcome = router.text(frame, "outcome")
    net_r = router.numeric(frame, "net_r")
    mfe = _first_numeric(
        frame,
        (
            "mfe_r",
            "mfe_net_r",
            "maximum_favorable_excursion_r",
            "max_favorable_excursion_r",
        ),
    )
    usable = ~outcome.eq("UNFILLED") & net_r.notna()
    mfe = mfe.where(mfe.notna(), np.where(net_r > 0.0, 1.0, 0.0))
    base = mfe.ge(1.0).astype(float)
    completion = net_r.gt(0.0).astype(float)
    return usable, base, completion


def _episode_weights(frame: pd.DataFrame) -> np.ndarray:
    keys = router.text(frame, "research_period") + "::" + router.text(
        frame, "episode_id"
    )
    counts = keys.map(keys.value_counts()).astype(float).clip(lower=1.0)
    return (1.0 / counts).to_numpy(float)


def _robust_transform(
    features: pd.DataFrame,
    median: np.ndarray | None = None,
    scale: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    matrix = features.to_numpy(float)
    if median is None:
        median = np.nanmedian(matrix, axis=0)
        median = np.where(np.isfinite(median), median, 0.0)
    filled = np.where(np.isfinite(matrix), matrix, median)
    if scale is None:
        q25 = np.nanpercentile(filled, 25.0, axis=0)
        q75 = np.nanpercentile(filled, 75.0, axis=0)
        scale = q75 - q25
        fallback = np.nanstd(filled, axis=0)
        scale = np.where(scale > 1e-9, scale, fallback)
        scale = np.where(scale > 1e-9, scale, 1.0)
    transformed = np.clip((filled - median) / scale, -8.0, 8.0)
    return transformed, median.astype(float), scale.astype(float)


def _fit_logistic(
    matrix: np.ndarray,
    target: np.ndarray,
    sample_weight: np.ndarray,
    *,
    l2: float = 2.5,
) -> np.ndarray:
    design = np.column_stack([np.ones(len(matrix), dtype=float), matrix])
    target = np.asarray(target, dtype=float)
    weight = np.asarray(sample_weight, dtype=float)
    weight = weight / max(weight.sum(), 1e-12)
    positive = float(np.sum(weight * target))
    if positive <= 1e-8 or positive >= 1.0 - 1e-8:
        intercept = math.log(
            np.clip(positive, 1e-5, 1.0 - 1e-5)
            / np.clip(1.0 - positive, 1e-5, 1.0)
        )
        result = np.zeros(design.shape[1], dtype=float)
        result[0] = intercept
        return result

    coefficients = np.zeros(design.shape[1], dtype=float)
    coefficients[0] = math.log(positive / (1.0 - positive))
    penalty = np.eye(design.shape[1], dtype=float) * float(l2)
    penalty[0, 0] = 1e-8
    for _ in range(60):
        linear = np.clip(design @ coefficients, -30.0, 30.0)
        probability = 1.0 / (1.0 + np.exp(-linear))
        residual = (probability - target) * weight
        gradient = design.T @ residual + penalty @ coefficients
        curvature = weight * probability * (1.0 - probability)
        hessian = (design.T * curvature) @ design + penalty
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hessian) @ gradient
        norm = float(np.linalg.norm(step))
        if norm > 2.0:
            step *= 2.0 / norm
        coefficients -= step
        if float(np.max(np.abs(step))) < 1e-7:
            break
    return coefficients


def fit_model(frame: pd.DataFrame) -> dict[str, Any]:
    usable, base_label, completion_label = _labels(frame)
    train = frame.loc[usable].copy()
    if len(train) < 20:
        raise ValueError(f"Only {len(train)} filled arm states available for fitting")
    features = router.raw_feature_frame(train)
    matrix, median, scale = _robust_transform(features)
    weights = _episode_weights(train)
    base_coef = _fit_logistic(
        matrix,
        base_label.loc[usable].to_numpy(float),
        weights,
        l2=3.0,
    )
    completion_coef = _fit_logistic(
        matrix,
        completion_label.loc[usable].to_numpy(float),
        weights,
        l2=3.5,
    )
    return {
        "feature_names": list(router.FEATURE_NAMES),
        "median": median.tolist(),
        "scale": scale.tolist(),
        "coef_base_excursion": base_coef.tolist(),
        "coef_structural_completion": completion_coef.tolist(),
        "base_weight": 0.58,
        "training_filled_arm_states": int(len(train)),
        "training_independent_episodes": int(
            (
                router.text(train, "research_period")
                + "::"
                + router.text(train, "episode_id")
            ).nunique()
        ),
    }


def _apply_model(frame: pd.DataFrame, model: dict[str, Any]) -> pd.DataFrame:
    return router.score_actions(frame, {"model": model, "selection": {}})


def _candidate_metrics(
    scored: pd.DataFrame,
    bounds: dict[str, dict[str, str]],
    selection: dict[str, Any],
) -> dict[str, Any]:
    policy = {"model": {}, "selection": selection}
    plans = router.select_plans(scored, policy, pre_scored=True)
    orders, trades = router.route_continuous_account(plans)
    overall = router.metric_block(orders, trades)
    calendar_days = sum(
        (date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days
        for w in bounds.values()
    )
    overall["calendar_days"] = int(calendar_days)
    overall["closed_trades_per_calendar_day"] = (
        overall["closed_trades"] / max(calendar_days, 1)
    )
    period_blocks: dict[str, dict[str, Any]] = {}
    for period in bounds:
        period_orders = (
            orders[router.text(orders, "research_period").eq(period)]
            if len(orders)
            else orders
        )
        period_trades = (
            trades[router.text(trades, "research_period").eq(period)]
            if len(trades)
            else trades
        )
        period_blocks[period] = router.metric_block(period_orders, period_trades)
    return {
        "overall": overall,
        "periods": period_blocks,
        "plans": plans,
        "orders": orders,
        "trades": trades,
    }


def _selection_key(result: dict[str, Any], bounds: dict[str, Any]) -> tuple[float, ...]:
    overall = result["overall"]
    periods = list(result["periods"].values())
    closed = int(overall["closed_trades"])
    calendar_days = int(overall["calendar_days"])
    frequency = float(overall["closed_trades_per_calendar_day"])
    mean_r = float(overall["mean_net_r"])
    base_rate = float(overall["base_excursion_1r_rate"])
    dd = float(overall["max_drawdown"])
    period_sums = [float(block["sum_net_r"]) for block in periods]
    period_means = [
        float(block["mean_net_r"]) if int(block["closed_trades"]) else -1.0
        for block in periods
    ]
    period_counts = [int(block["closed_trades"]) for block in periods]
    positive_periods = sum(value > 0.0 for value in period_sums)
    usable_periods = sum(value >= 3 for value in period_counts)
    worst_mean = min(period_means) if period_means else -1.0
    median_mean = float(np.median(period_means)) if period_means else -1.0
    frequency_shortfall = max(0.0, 0.85 - frequency)
    count_shortfall = max(0.0, calendar_days * 0.70 - closed) / max(
        calendar_days, 1
    )
    objective = (
        1.75 * worst_mean
        + 1.10 * median_mean
        + 1.25 * mean_r
        + 0.90 * (base_rate - 0.55)
        + 0.18 * min(frequency, 1.5)
        - 1.50 * dd
        - 1.10 * frequency_shortfall
        - 0.80 * count_shortfall
    )
    return (
        float(usable_periods),
        float(positive_periods),
        float(objective),
        float(worst_mean),
        float(mean_r),
        float(base_rate),
        float(min(frequency, 2.0)),
        float(-dd),
    )


def choose_selection(
    scored: pd.DataFrame,
    bounds: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    baseline = (
        router.numeric(scored, "gross_rr").ge(1.0)
        & router.numeric(scored, "planned_target_net_r").ge(0.25)
        & router.numeric(scored, "trigger_score").notna()
    )
    scores = router.numeric(scored.loc[baseline], "trigger_score").dropna()
    if scores.empty:
        raise ValueError("No causal arm state has an executable structural target")
    quantiles = sorted(
        {float(scores.quantile(q)) for q in np.linspace(0.35, 0.93, 18)}
    )

    best_selection: dict[str, Any] | None = None
    best_result: dict[str, Any] | None = None
    best_key: tuple[float, ...] | None = None
    search_rows: list[dict[str, Any]] = []
    grids = itertools.product(
        (0.10, 0.18, 0.28, 0.38),
        (0.48, 0.58, 0.68, 0.76),
        (0.38, 0.50, 0.62),
        (0.32, 0.42, 0.52),
        (0.04, 0.08, 0.13),
        quantiles,
    )
    for (
        min_progress,
        max_consumed,
        max_retrace,
        min_acceptance,
        min_path,
        threshold,
    ) in grids:
        selection = {
            "minimum_net_completion_r": 0.25,
            "minimum_progress_r": float(min_progress),
            "minimum_consumed_fraction": 0.0,
            "maximum_consumed_fraction": float(max_consumed),
            "minimum_headroom_r": 0.35,
            "maximum_current_retrace_fraction": float(max_retrace),
            "minimum_acceptance_ratio": float(min_acceptance),
            "minimum_path_efficiency": float(min_path),
            "score_threshold": float(threshold),
            "allowed_phases": [
                "EARLY_RESPONSE",
                "ACCEPTED_EXPANSION",
                "FIRST_RETEST_FORMING",
            ],
        }
        result = _candidate_metrics(scored, bounds, selection)
        key = _selection_key(result, bounds)
        overall = result["overall"]
        search_rows.append(
            {
                **selection,
                "closed_trades": overall["closed_trades"],
                "trades_per_day": overall["closed_trades_per_calendar_day"],
                "win_rate": overall["win_rate"],
                "base_excursion_1r_rate": overall["base_excursion_1r_rate"],
                "sum_net_r": overall["sum_net_r"],
                "mean_net_r": overall["mean_net_r"],
                "max_drawdown": overall["max_drawdown"],
                "positive_periods": sum(
                    block["sum_net_r"] > 0.0
                    for block in result["periods"].values()
                ),
                "minimum_period_mean_r": min(
                    block["mean_net_r"] if block["closed_trades"] else -1.0
                    for block in result["periods"].values()
                ),
                "selection_key": list(key),
            }
        )
        if best_key is None or key > best_key:
            best_key = key
            best_selection = selection
            best_result = result

    assert best_selection is not None and best_result is not None
    search = pd.DataFrame(search_rows).sort_values(
        [
            "positive_periods",
            "minimum_period_mean_r",
            "mean_net_r",
            "base_excursion_1r_rate",
            "trades_per_day",
        ],
        ascending=[False, False, False, False, False],
        kind="mergesort",
    )
    return best_selection, best_result, search


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period-bounds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    bounds = json.loads(args.period_bounds.read_text(encoding="utf-8"))
    actions = router.load_actions(args.root, bounds)
    periods = sorted(bounds, key=lambda name: bounds[name]["start"])

    oof_parts: list[pd.DataFrame] = []
    fold_records: list[dict[str, Any]] = []
    for held_out in periods:
        train = actions[~router.text(actions, "research_period").eq(held_out)].copy()
        test = actions[router.text(actions, "research_period").eq(held_out)].copy()
        model = fit_model(train)
        scored = _apply_model(test, model)
        oof_parts.append(scored)
        fold_records.append(
            {
                "held_out_period": held_out,
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "training_filled_arm_states": model["training_filled_arm_states"],
                "training_independent_episodes": model[
                    "training_independent_episodes"
                ],
            }
        )
    oof = pd.concat(oof_parts, ignore_index=True, sort=False)
    selection, result, search = choose_selection(oof, bounds)
    final_model = fit_model(actions)
    policy = {
        "policy": "ML_EASYCHART_B_V4_FIXED_TWO_HEAD_CAUSAL_TRIGGER",
        "fit_contract": {
            "development_periods": periods,
            "leave_one_period_out_threshold_selection": True,
            "symbol_identity_used": False,
            "calendar_identity_used": False,
            "outcome_fields_used_at_runtime": False,
            "label_base_excursion": "MFE before stop >= +1R",
            "label_structural_completion": "declared natural target before stop",
            "fixed_r_target_cap": False,
        },
        "model": final_model,
        "selection": selection,
    }
    plans = result["plans"]
    orders = result["orders"]
    trades = result["trades"]
    summary = router.build_summary(actions, plans, orders, trades, bounds, policy)
    summary["evaluation_kind"] = "LEAVE_ONE_PERIOD_OUT_DEVELOPMENT"
    summary["folds"] = fold_records
    summary["selection_key"] = list(_selection_key(result, bounds))

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    plans.to_csv(args.output / "eligible_plans.csv", index=False)
    orders.to_csv(args.output / "selected_orders.csv", index=False)
    trades.to_csv(args.output / "closed_trades.csv", index=False)
    search.head(250).to_csv(args.output / "top_search.csv", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
