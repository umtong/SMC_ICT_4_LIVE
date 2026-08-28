#!/usr/bin/env python3
"""Fit/freeze the V5 three-head causal trigger on development windows only."""
from __future__ import annotations

import argparse
from datetime import date
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import easychart_b_v4 as feature_base
import easychart_b_v5 as router
import fit_trigger_v4 as fit4


def _labels(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    outcome = router.text(frame, "outcome")
    net_r = router.numeric(frame, "net_r")
    mfe = router.numeric(frame, "mfe_r")
    for alias in ("mfe_net_r", "maximum_favorable_excursion_r", "max_favorable_excursion_r"):
        if mfe.notna().any():
            break
        mfe = router.numeric(frame, alias)
    usable = ~outcome.eq("UNFILLED") & net_r.notna()
    mfe = mfe.where(mfe.notna(), np.where(net_r > 0.0, 1.0, 0.0))
    base_excursion = mfe.ge(1.0).astype(float)
    completion = net_r.gt(0.0).astype(float)
    earliest = pd.Series(0.0, index=frame.index, dtype=float)
    ordered = frame.assign(
        _usable=usable,
        _good=(usable & base_excursion.eq(1.0) & completion.eq(1.0)),
    ).sort_values(
        ["research_period", "episode_id", "order_time_ns", "action_id"],
        kind="mergesort",
    )
    for _, episode in ordered.groupby(["research_period", "episode_id"], sort=False):
        good = episode.index[episode["_good"]]
        if len(good):
            earliest.loc[good[0]] = 1.0
    return usable, base_excursion, completion, earliest


def _balanced_episode_weights(frame: pd.DataFrame, target: pd.Series) -> np.ndarray:
    key = router.text(frame, "research_period") + "::" + router.text(frame, "episode_id")
    counts = key.map(key.value_counts()).astype(float).clip(lower=1.0)
    weights = 1.0 / counts
    positive = target.eq(1.0)
    negative = ~positive
    if positive.any() and negative.any():
        weights.loc[positive] *= 0.5 / max(float(weights.loc[positive].sum()), 1e-12)
        weights.loc[negative] *= 0.5 / max(float(weights.loc[negative].sum()), 1e-12)
    return weights.to_numpy(float)


def _fit_one(frame: pd.DataFrame) -> dict[str, Any]:
    usable, base_label, completion_label, timing_label = _labels(frame)
    train = frame.loc[usable].copy()
    if len(train) < 20:
        raise ValueError(f"Only {len(train)} filled arm states available")
    features = router.raw_feature_frame(train)
    matrix, median, scale = fit4._robust_transform(features)
    base_target = base_label.loc[usable]
    completion_target = completion_label.loc[usable]
    timing_target = timing_label.loc[usable]
    return {
        "feature_names": list(features.columns),
        "median": median.tolist(),
        "scale": scale.tolist(),
        "coef_base_excursion": fit4._fit_logistic(
            matrix,
            base_target.to_numpy(float),
            _balanced_episode_weights(train, base_target),
            l2=0.010,
        ).tolist(),
        "coef_structural_completion": fit4._fit_logistic(
            matrix,
            completion_target.to_numpy(float),
            _balanced_episode_weights(train, completion_target),
            l2=0.014,
        ).tolist(),
        "coef_earliest_good_arm": fit4._fit_logistic(
            matrix,
            timing_target.to_numpy(float),
            _balanced_episode_weights(train, timing_target),
            l2=0.020,
        ).tolist(),
        "training_filled_arm_states": int(len(train)),
        "training_independent_episodes": int(
            (
                router.text(train, "research_period")
                + "::"
                + router.text(train, "episode_id")
            ).nunique()
        ),
        "base_positive_rate": float(base_target.mean()),
        "completion_positive_rate": float(completion_target.mean()),
        "timing_positive_rate": float(timing_target.mean()),
    }


def fit_policy_models(frame: pd.DataFrame) -> dict[str, Any]:
    attributed = router.attach_mechanism(frame)
    causal = attributed[router.text(attributed, "mechanism").ne("")].copy()
    if len(causal) < 20:
        causal = attributed
    global_model = _fit_one(causal)
    by_mechanism: dict[str, dict[str, Any]] = {}
    usable, base_label, completion_label, _ = _labels(attributed)
    for name in router.MECHANISM_PRIORITY:
        subset = attributed[router.text(attributed, "mechanism").eq(name)].copy()
        subset_usable = usable.reindex(subset.index, fill_value=False)
        if (
            int(subset_usable.sum()) >= 45
            and base_label.reindex(subset.index)[subset_usable].nunique() > 1
            and completion_label.reindex(subset.index)[subset_usable].nunique() > 1
            and (
                router.text(subset, "research_period")
                + "::"
                + router.text(subset, "episode_id")
            ).nunique()
            >= 12
        ):
            by_mechanism[name] = _fit_one(subset)
    return {"global": global_model, "by_mechanism": by_mechanism}


def _score(frame: pd.DataFrame, models: dict[str, Any]) -> pd.DataFrame:
    return router.score_actions(
        frame,
        {
            "models": models,
            "score_weights": {"base": 0.42, "completion": 0.36, "early": 0.22},
            "selection": {},
        },
    )


def _evaluate(
    scored: pd.DataFrame,
    bounds: dict[str, dict[str, str]],
    selection: dict[str, Any],
) -> dict[str, Any]:
    plans = router.select_plans(scored, {"selection": selection}, pre_scored=True)
    orders, trades = router.route_continuous_account(plans)
    overall = feature_base.metric_block(orders, trades)
    days = sum(
        (date.fromisoformat(value["end"]) - date.fromisoformat(value["start"])).days
        for value in bounds.values()
    )
    overall["calendar_days"] = int(days)
    overall["closed_trades_per_calendar_day"] = overall["closed_trades"] / max(days, 1)
    periods: dict[str, dict[str, Any]] = {}
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
        periods[period] = feature_base.metric_block(period_orders, period_trades)
    return {
        "overall": overall,
        "periods": periods,
        "plans": plans,
        "orders": orders,
        "trades": trades,
    }


def _key(result: dict[str, Any]) -> tuple[float, ...]:
    overall = result["overall"]
    periods = list(result["periods"].values())
    period_means = [
        float(block["mean_net_r"]) if int(block["closed_trades"]) else -1.0
        for block in periods
    ]
    period_sums = [float(block["sum_net_r"]) for block in periods]
    period_counts = [int(block["closed_trades"]) for block in periods]
    frequency = float(overall["closed_trades_per_calendar_day"])
    mean_r = float(overall["mean_net_r"])
    base_rate = float(overall.get("base_excursion_1r_rate", 0.0))
    drawdown = float(overall["max_drawdown"])
    robust_periods = sum(
        count >= 3 and total > 0.0
        for count, total in zip(period_counts, period_sums, strict=True)
    )
    positive_periods = sum(total > 0.0 for total in period_sums)
    worst_mean = min(period_means) if period_means else -1.0
    median_mean = float(np.median(period_means)) if period_means else -1.0
    objective = (
        2.00 * worst_mean
        + 1.20 * median_mean
        + 1.35 * mean_r
        + 0.85 * (base_rate - 0.55)
        + 0.22 * min(frequency, 1.5)
        - 1.60 * drawdown
        - 1.20 * max(0.0, 0.75 - frequency)
    )
    return (
        float(robust_periods),
        float(positive_periods),
        float(objective),
        float(worst_mean),
        float(mean_r),
        float(base_rate),
        float(min(frequency, 2.0)),
        float(-drawdown),
    )


def choose_selection(
    scored: pd.DataFrame,
    bounds: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    baseline = (
        router.text(scored, "mechanism").ne("")
        & router.numeric(scored, "gross_rr").ge(1.0)
        & router.numeric(scored, "planned_target_net_r").ge(0.25)
        & router.numeric(scored, "trigger_score").notna()
    )
    scores = router.numeric(scored.loc[baseline], "trigger_score").dropna()
    if scores.empty:
        raise ValueError("No executable causal-mechanism arm states")
    thresholds = sorted(
        {float(scores.quantile(quantile)) for quantile in np.linspace(0.32, 0.93, 13)}
    )
    best_selection: dict[str, Any] | None = None
    best_result: dict[str, Any] | None = None
    best_key: tuple[float, ...] | None = None
    rows: list[dict[str, Any]] = []
    for (
        minimum_progress,
        maximum_consumed,
        maximum_retrace,
        minimum_acceptance,
        minimum_path,
        threshold,
    ) in itertools.product(
        (0.10, 0.20, 0.32),
        (0.46, 0.58, 0.70),
        (0.40, 0.55, 0.70),
        (0.32, 0.42, 0.52),
        (0.04, 0.09),
        thresholds,
    ):
        selection = {
            "minimum_net_completion_r": 0.25,
            "minimum_progress_r": float(minimum_progress),
            "minimum_consumed_fraction": 0.0,
            "maximum_consumed_fraction": float(maximum_consumed),
            "minimum_headroom_r": 0.35,
            "maximum_current_retrace_fraction": float(maximum_retrace),
            "minimum_acceptance_ratio": float(minimum_acceptance),
            "minimum_path_efficiency": float(minimum_path),
            "minimum_base_probability": 0.42,
            "minimum_completion_probability": 0.38,
            "score_threshold": float(threshold),
            "allowed_phases": [
                "EARLY_RESPONSE",
                "ACCEPTED_EXPANSION",
                "FIRST_RETEST_FORMING",
            ],
        }
        result = _evaluate(scored, bounds, selection)
        key = _key(result)
        overall = result["overall"]
        rows.append(
            {
                **{
                    name: value
                    for name, value in selection.items()
                    if not isinstance(value, list)
                },
                "closed_trades": overall["closed_trades"],
                "trades_per_day": overall["closed_trades_per_calendar_day"],
                "win_rate": overall["win_rate"],
                "base_excursion_1r_rate": overall.get("base_excursion_1r_rate", 0.0),
                "sum_net_r": overall["sum_net_r"],
                "mean_net_r": overall["mean_net_r"],
                "max_drawdown": overall["max_drawdown"],
                "positive_periods": sum(
                    block["sum_net_r"] > 0.0
                    for block in result["periods"].values()
                ),
                "robust_periods": sum(
                    block["closed_trades"] >= 3 and block["sum_net_r"] > 0.0
                    for block in result["periods"].values()
                ),
                "selection_key": json.dumps(key),
            }
        )
        if best_key is None or key > best_key:
            best_key = key
            best_selection = selection
            best_result = result
    assert best_selection is not None and best_result is not None
    search = pd.DataFrame(rows).sort_values(
        [
            "robust_periods",
            "positive_periods",
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
    actions = feature_base.load_actions(args.root, bounds)
    periods = sorted(bounds, key=lambda name: bounds[name]["start"])
    oof_parts: list[pd.DataFrame] = []
    fold_records: list[dict[str, Any]] = []
    for held_out in periods:
        train = actions[~router.text(actions, "research_period").eq(held_out)].copy()
        test = actions[router.text(actions, "research_period").eq(held_out)].copy()
        models = fit_policy_models(train)
        oof_parts.append(_score(test, models))
        fold_records.append(
            {
                "held_out_period": held_out,
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "global_training_filled_arm_states": models["global"][
                    "training_filled_arm_states"
                ],
                "mechanism_models": sorted(models["by_mechanism"]),
            }
        )
    oof = pd.concat(oof_parts, ignore_index=True, sort=False)
    selection, result, search = choose_selection(oof, bounds)
    final_models = fit_policy_models(actions)
    policy = {
        "policy": router.POLICY_NAME,
        "fit_contract": {
            "development_periods": periods,
            "leave_one_period_out_selection": True,
            "symbol_identity_used": False,
            "calendar_identity_used": False,
            "outcome_fields_used_at_runtime": False,
            "fixed_r_target_cap": False,
            "base_label": "MFE before stop >= +1R",
            "completion_label": "declared natural structure completes before stop",
            "timing_label": "earliest arm in episode satisfying both labels",
        },
        "models": final_models,
        "score_weights": {"base": 0.42, "completion": 0.36, "early": 0.22},
        "selection": selection,
    }
    plans = result["plans"]
    orders = result["orders"]
    trades = result["trades"]
    summary = router.build_summary(actions, plans, orders, trades, bounds, policy)
    summary["evaluation_kind"] = "LEAVE_ONE_PERIOD_OUT_DEVELOPMENT"
    summary["folds"] = fold_records
    summary["selection_key"] = list(_key(result))

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
