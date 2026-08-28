#!/usr/bin/env python3
"""Cross-fit and freeze V7 nonlinear causal trigger heads."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier

import easychart_b_v4 as base
import easychart_b_v6 as expert
import easychart_b_v7 as router


def labels(
    frame: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    outcome = base.text(frame, "outcome")
    net_r = base.numeric(frame, "net_r")
    mfe = base.numeric(frame, "mfe_r")
    for alias in (
        "mfe_net_r",
        "maximum_favorable_excursion_r",
        "max_favorable_excursion_r",
    ):
        if mfe.notna().any():
            break
        mfe = base.numeric(frame, alias)
    usable = ~outcome.eq("UNFILLED") & net_r.notna()
    mfe = mfe.where(mfe.notna(), np.where(net_r > 0.0, 1.0, 0.0))
    base_excursion = mfe.ge(1.0).astype(int)
    completion = net_r.gt(0.0).astype(int)
    earliest = pd.Series(0, index=frame.index, dtype=int)
    ordered = frame.assign(
        _good=(usable & base_excursion.eq(1) & completion.eq(1))
    ).sort_values(
        ["research_period", "episode_id", "order_time_ns", "action_id"],
        kind="mergesort",
    )
    for _, episode in ordered.groupby(["research_period", "episode_id"], sort=False):
        good = episode.index[episode["_good"]]
        if len(good):
            earliest.loc[good[0]] = 1
    return usable, base_excursion, completion, earliest


def sample_weights(frame: pd.DataFrame, target: pd.Series) -> np.ndarray:
    key = base.text(frame, "research_period") + "::" + base.text(
        frame, "episode_id"
    )
    weights = 1.0 / key.map(key.value_counts()).astype(float).clip(lower=1.0)
    positive = target.eq(1)
    negative = ~positive
    if positive.any() and negative.any():
        weights.loc[positive] *= 0.5 / max(
            float(weights.loc[positive].sum()), 1e-12
        )
        weights.loc[negative] *= 0.5 / max(
            float(weights.loc[negative].sum()), 1e-12
        )
    return weights.to_numpy(float)


def fit_head(
    matrix: np.ndarray,
    target: np.ndarray,
    weights: np.ndarray,
    seed: int,
) -> Any:
    if pd.Series(target).nunique() < 2:
        model = DummyClassifier(strategy="constant", constant=int(target[0]))
        model.fit(matrix, target)
        return model
    model = HistGradientBoostingClassifier(
        loss="log_loss",
        learning_rate=0.055,
        max_iter=110,
        max_leaf_nodes=9,
        max_depth=3,
        min_samples_leaf=35,
        l2_regularization=2.0,
        max_bins=63,
        early_stopping=False,
        random_state=seed,
    )
    model.fit(matrix, target, sample_weight=weights)
    return model


def fit_bundle(frame: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    attributed = expert.attach_experts(frame)
    attributed = attributed[
        base.numeric(attributed, "expert_count", 0.0).ge(1.0)
    ].copy()
    usable, base_label, completion_label, timing_label = labels(attributed)
    train = attributed.loc[usable].copy()
    if len(train) < 40:
        raise ValueError(f"Only {len(train)} filled expert-voted arms")
    features = expert.raw_feature_frame(train)
    matrix = features.to_numpy(float)
    targets = [
        base_label.loc[usable],
        completion_label.loc[usable],
        timing_label.loc[usable],
    ]
    models: dict[str, Any] = {}
    for name, target, seed in zip(
        ("base_excursion", "structural_completion", "earliest_good_arm"),
        targets,
        (17, 29, 43),
        strict=True,
    ):
        models[name] = fit_head(
            matrix,
            target.to_numpy(int),
            sample_weights(train, target),
            seed,
        )
    metadata = {
        "feature_names": list(features.columns),
        "training_filled_arm_states": int(len(train)),
        "training_independent_episodes": int(
            (
                base.text(train, "research_period")
                + "::"
                + base.text(train, "episode_id")
            ).nunique()
        ),
        "base_positive_rate": float(targets[0].mean()),
        "completion_positive_rate": float(targets[1].mean()),
        "timing_positive_rate": float(targets[2].mean()),
    }
    return models, metadata


def score(
    frame: pd.DataFrame,
    models: dict[str, Any],
    metadata: dict[str, Any],
) -> pd.DataFrame:
    return router.score_actions(
        frame,
        {
            "feature_names": metadata["feature_names"],
            "score_weights": {"base": 0.46, "completion": 0.34, "early": 0.20},
            "selection": {},
        },
        models,
    )


def evaluate(
    scored: pd.DataFrame,
    bounds: dict[str, dict[str, str]],
    selection: dict[str, Any],
) -> dict[str, Any]:
    plans = expert.select_plans(
        scored,
        {"selection": selection},
        pre_scored=True,
    )
    orders, trades = expert.route_continuous_account(plans)
    overall = base.metric_block(orders, trades)
    calendar_days = sum(
        (date.fromisoformat(value["end"]) - date.fromisoformat(value["start"])).days
        for value in bounds.values()
    )
    overall["calendar_days"] = int(calendar_days)
    overall["closed_trades_per_calendar_day"] = (
        overall["closed_trades"] / max(calendar_days, 1)
    )
    periods: dict[str, dict[str, Any]] = {}
    for period in bounds:
        period_orders = (
            orders[base.text(orders, "research_period").eq(period)]
            if len(orders)
            else orders
        )
        period_trades = (
            trades[base.text(trades, "research_period").eq(period)]
            if len(trades)
            else trades
        )
        periods[period] = base.metric_block(period_orders, period_trades)
    return {
        "overall": overall,
        "periods": periods,
        "plans": plans,
        "orders": orders,
        "trades": trades,
    }


def selection_key(result: dict[str, Any]) -> tuple[float, ...]:
    overall = result["overall"]
    blocks = list(result["periods"].values())
    means = [
        float(block["mean_net_r"]) if block["closed_trades"] else -1.0
        for block in blocks
    ]
    sums = [float(block["sum_net_r"]) for block in blocks]
    counts = [int(block["closed_trades"]) for block in blocks]
    frequency = float(overall["closed_trades_per_calendar_day"])
    mean_r = float(overall["mean_net_r"])
    base_rate = float(overall.get("base_excursion_1r_rate", 0.0))
    drawdown = float(overall["max_drawdown"])
    robust_periods = sum(
        count >= 4 and total > 0.0
        for count, total in zip(counts, sums, strict=True)
    )
    positive_periods = sum(total > 0.0 for total in sums)
    worst_mean = min(means) if means else -1.0
    lower_quartile = float(np.quantile(means, 0.25)) if means else -1.0
    objective = (
        2.3 * worst_mean
        + 1.35 * lower_quartile
        + 1.45 * mean_r
        + 1.0 * (base_rate - 0.60)
        + 0.24 * min(frequency, 1.6)
        - 1.7 * drawdown
        - 1.3 * max(0.0, 0.85 - frequency)
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
        base.numeric(scored, "expert_count", 0.0).ge(1.0)
        & base.numeric(scored, "gross_rr").ge(1.0)
        & base.numeric(scored, "planned_target_net_r").ge(0.25)
    )
    scores = base.numeric(scored.loc[baseline], "trigger_score").dropna()
    if scores.empty:
        raise ValueError("No expert-voted structural arms")
    thresholds = sorted(
        {float(scores.quantile(quantile)) for quantile in np.linspace(0.25, 0.96, 16)}
    )
    base_probabilities = base.numeric(
        scored.loc[baseline], "base_excursion_probability"
    ).dropna()
    completion_probabilities = base.numeric(
        scored.loc[baseline], "structural_completion_probability"
    ).dropna()
    base_floors = sorted(
        {float(base_probabilities.quantile(quantile)) for quantile in (0.20, 0.35, 0.50)}
    )
    completion_floors = sorted(
        {
            float(completion_probabilities.quantile(quantile))
            for quantile in (0.15, 0.30, 0.45)
        }
    )
    choices = (
        (0.06, 0.14, 0.24, 0.34),
        (0.44, 0.56, 0.68),
        (0.38, 0.52, 0.68),
        (0.28, 0.38, 0.48),
        (0.02, 0.07),
        (1.0, 2.0),
        base_floors,
        completion_floors,
        thresholds,
    )
    rng = np.random.default_rng(73)
    configurations: set[tuple[float, ...]] = set()
    while len(configurations) < 1800:
        configurations.add(tuple(float(rng.choice(values)) for values in choices))
    configurations.update(
        {
            (
                0.06,
                0.68,
                0.68,
                0.28,
                0.02,
                1.0,
                min(base_floors),
                min(completion_floors),
                min(thresholds),
            ),
            (
                0.24,
                0.56,
                0.52,
                0.38,
                0.07,
                1.0,
                base_floors[1],
                completion_floors[1],
                thresholds[len(thresholds) // 2],
            ),
            (
                0.34,
                0.44,
                0.38,
                0.48,
                0.07,
                2.0,
                max(base_floors),
                max(completion_floors),
                max(thresholds),
            ),
        }
    )
    best: tuple[tuple[float, ...], dict[str, Any], dict[str, Any]] | None = None
    rows: list[dict[str, Any]] = []
    for (
        minimum_progress,
        maximum_consumed,
        maximum_retrace,
        minimum_acceptance,
        minimum_path,
        minimum_experts,
        base_floor,
        completion_floor,
        threshold,
    ) in sorted(configurations):
        selection = {
            "minimum_expert_count": float(minimum_experts),
            "minimum_net_completion_r": 0.25,
            "minimum_progress_r": float(minimum_progress),
            "maximum_consumed_fraction": float(maximum_consumed),
            "minimum_headroom_r": 0.35,
            "maximum_current_retrace_fraction": float(maximum_retrace),
            "minimum_acceptance_ratio": float(minimum_acceptance),
            "minimum_path_efficiency": float(minimum_path),
            "minimum_base_probability": float(base_floor),
            "minimum_completion_probability": float(completion_floor),
            "score_threshold": float(threshold),
            "allowed_phases": [
                "EARLY_RESPONSE",
                "ACCEPTED_EXPANSION",
                "FIRST_RETEST_FORMING",
            ],
        }
        result = evaluate(scored, bounds, selection)
        key = selection_key(result)
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
                "selection_key": json.dumps(key),
            }
        )
        if best is None or key > best[0]:
            best = (key, selection, result)
    assert best is not None
    search = pd.DataFrame(rows).sort_values(
        ["mean_net_r", "base_excursion_1r_rate", "trades_per_day"],
        ascending=[False, False, False],
        kind="mergesort",
    )
    return best[1], best[2], search


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period-bounds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    bounds = json.loads(args.period_bounds.read_text(encoding="utf-8"))
    actions = base.load_actions(args.root, bounds)
    periods = sorted(bounds, key=lambda name: bounds[name]["start"])
    oof_parts: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    for held_out in periods:
        train = actions[~base.text(actions, "research_period").eq(held_out)].copy()
        test = actions[base.text(actions, "research_period").eq(held_out)].copy()
        models, metadata = fit_bundle(train)
        oof_parts.append(score(test, models, metadata))
        folds.append(
            {
                "held_out_period": held_out,
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                **{
                    key: value
                    for key, value in metadata.items()
                    if key != "feature_names"
                },
            }
        )
    oof = pd.concat(oof_parts, ignore_index=True, sort=False)
    selection, result, search = choose_selection(oof, bounds)
    models, metadata = fit_bundle(actions)
    policy = {
        "policy": router.POLICY_NAME,
        "fit_contract": {
            "development_periods": periods,
            "leave_one_period_out_selection": True,
            "expert_inputs_symbol_neutral": True,
            "expert_inputs_outcome_free": True,
            "fixed_r_target_cap": False,
            "base_label": "MFE before stop >= +1R",
            "completion_label": "declared natural structure completes",
            "timing_label": "earliest arm satisfying both",
        },
        "feature_names": metadata["feature_names"],
        "model_metadata": {
            key: value
            for key, value in metadata.items()
            if key != "feature_names"
        },
        "score_weights": {"base": 0.46, "completion": 0.34, "early": 0.20},
        "selection": selection,
    }
    summary = router.build_summary(
        actions,
        result["plans"],
        result["orders"],
        result["trades"],
        bounds,
        policy,
    )
    summary["evaluation_kind"] = "LEAVE_ONE_PERIOD_OUT_DEVELOPMENT"
    summary["folds"] = folds
    summary["selection_key"] = list(selection_key(result))

    args.output.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, args.output / "models.joblib", compress=3)
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
    result["plans"].to_csv(args.output / "eligible_plans.csv", index=False)
    result["orders"].to_csv(args.output / "selected_orders.csv", index=False)
    result["trades"].to_csv(args.output / "closed_trades.csv", index=False)
    search.head(250).to_csv(args.output / "top_search.csv", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
