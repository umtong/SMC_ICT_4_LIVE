#!/usr/bin/env python3
"""Fit V6 expert-distilled three-head trigger with leave-period-out predictions."""
from __future__ import annotations

import argparse
from datetime import date
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import easychart_b_v4 as base
import easychart_b_v6 as router
import fit_trigger_v4 as fit4


def labels(
    frame: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    outcome = router.text(frame, "outcome")
    net_r = router.numeric(frame, "net_r")
    mfe = router.numeric(frame, "mfe_r")
    for alias in (
        "mfe_net_r",
        "maximum_favorable_excursion_r",
        "max_favorable_excursion_r",
    ):
        if mfe.notna().any():
            break
        mfe = router.numeric(frame, alias)
    usable = ~outcome.eq("UNFILLED") & net_r.notna()
    mfe = mfe.where(mfe.notna(), np.where(net_r > 0.0, 1.0, 0.0))
    base_excursion = mfe.ge(1.0).astype(float)
    completion = net_r.gt(0.0).astype(float)
    earliest = pd.Series(0.0, index=frame.index, dtype=float)
    ordered = frame.assign(
        _good=(usable & base_excursion.eq(1.0) & completion.eq(1.0))
    ).sort_values(
        ["research_period", "episode_id", "order_time_ns", "action_id"],
        kind="mergesort",
    )
    for _, episode in ordered.groupby(["research_period", "episode_id"], sort=False):
        good = episode.index[episode["_good"]]
        if len(good):
            earliest.loc[good[0]] = 1.0
    return usable, base_excursion, completion, earliest


def weights(frame: pd.DataFrame, target: pd.Series) -> np.ndarray:
    key = router.text(frame, "research_period") + "::" + router.text(
        frame, "episode_id"
    )
    sample_weight = 1.0 / key.map(key.value_counts()).astype(float).clip(lower=1.0)
    positive = target.eq(1.0)
    negative = ~positive
    if positive.any() and negative.any():
        sample_weight.loc[positive] *= 0.5 / max(
            float(sample_weight.loc[positive].sum()), 1e-12
        )
        sample_weight.loc[negative] *= 0.5 / max(
            float(sample_weight.loc[negative].sum()), 1e-12
        )
    return sample_weight.to_numpy(float)


def fit_model(frame: pd.DataFrame) -> dict[str, Any]:
    attributed = router.attach_experts(frame)
    attributed = attributed[
        router.numeric(attributed, "expert_count", 0.0).ge(1.0)
    ].copy()
    usable, base_label, completion_label, timing_label = labels(attributed)
    train = attributed.loc[usable].copy()
    if len(train) < 20:
        raise ValueError(f"Only {len(train)} filled expert-voted arms")
    features = router.raw_feature_frame(train)
    matrix, median, scale = fit4._robust_transform(features)
    targets = [
        base_label.loc[usable],
        completion_label.loc[usable],
        timing_label.loc[usable],
    ]
    coefficients = []
    for target, l2 in zip(targets, (0.010, 0.014, 0.020), strict=True):
        coefficients.append(
            fit4._fit_logistic(
                matrix,
                target.to_numpy(float),
                weights(train, target),
                l2=l2,
            ).tolist()
        )
    return {
        "feature_names": list(features.columns),
        "median": median.tolist(),
        "scale": scale.tolist(),
        "coef_base_excursion": coefficients[0],
        "coef_structural_completion": coefficients[1],
        "coef_earliest_good_arm": coefficients[2],
        "training_filled_arm_states": int(len(train)),
        "training_independent_episodes": int(
            (
                router.text(train, "research_period")
                + "::"
                + router.text(train, "episode_id")
            ).nunique()
        ),
        "base_positive_rate": float(targets[0].mean()),
        "completion_positive_rate": float(targets[1].mean()),
        "timing_positive_rate": float(targets[2].mean()),
    }


def score(frame: pd.DataFrame, model: dict[str, Any]) -> pd.DataFrame:
    return router.score_actions(
        frame,
        {
            "model": model,
            "score_weights": {"base": 0.44, "completion": 0.36, "early": 0.20},
            "selection": {},
        },
    )


def evaluate(
    scored: pd.DataFrame,
    bounds: dict[str, dict[str, str]],
    selection: dict[str, Any],
) -> dict[str, Any]:
    plans = router.select_plans(scored, {"selection": selection}, pre_scored=True)
    orders, trades = router.route_continuous_account(plans)
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
            orders[router.text(orders, "research_period").eq(period)]
            if len(orders)
            else orders
        )
        period_trades = (
            trades[router.text(trades, "research_period").eq(period)]
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
        count >= 3 and total > 0.0
        for count, total in zip(counts, sums, strict=True)
    )
    positive_periods = sum(total > 0.0 for total in sums)
    worst_mean = min(means) if means else -1.0
    median_mean = float(np.median(means)) if means else -1.0
    objective = (
        2.1 * worst_mean
        + 1.2 * median_mean
        + 1.4 * mean_r
        + 0.9 * (base_rate - 0.55)
        + 0.22 * min(frequency, 1.5)
        - 1.6 * drawdown
        - 1.2 * max(0.0, 0.8 - frequency)
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
        router.numeric(scored, "expert_count", 0.0).ge(1.0)
        & router.numeric(scored, "gross_rr").ge(1.0)
        & router.numeric(scored, "planned_target_net_r").ge(0.25)
    )
    scores = router.numeric(scored.loc[baseline], "trigger_score").dropna()
    if scores.empty:
        raise ValueError("No expert-voted structural arms")
    thresholds = sorted(
        {float(scores.quantile(quantile)) for quantile in np.linspace(0.30, 0.94, 14)}
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
        threshold,
    ) in itertools.product(
        (0.08, 0.16, 0.26, 0.36),
        (0.45, 0.57, 0.68),
        (0.40, 0.55, 0.70),
        (0.30, 0.40, 0.50),
        (0.03, 0.08),
        (1.0, 2.0),
        thresholds,
    ):
        selection = {
            "minimum_expert_count": float(minimum_experts),
            "minimum_net_completion_r": 0.25,
            "minimum_progress_r": float(minimum_progress),
            "maximum_consumed_fraction": float(maximum_consumed),
            "minimum_headroom_r": 0.35,
            "maximum_current_retrace_fraction": float(maximum_retrace),
            "minimum_acceptance_ratio": float(minimum_acceptance),
            "minimum_path_efficiency": float(minimum_path),
            "minimum_base_probability": 0.40,
            "minimum_completion_probability": 0.36,
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
    fold_records: list[dict[str, Any]] = []
    for held_out in periods:
        train = actions[~router.text(actions, "research_period").eq(held_out)].copy()
        test = actions[router.text(actions, "research_period").eq(held_out)].copy()
        model = fit_model(train)
        oof_parts.append(score(test, model))
        fold_records.append(
            {
                "held_out_period": held_out,
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "training_filled_arm_states": model["training_filled_arm_states"],
            }
        )
    oof = pd.concat(oof_parts, ignore_index=True, sort=False)
    selection, result, search = choose_selection(oof, bounds)
    model = fit_model(actions)
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
        "model": model,
        "score_weights": {"base": 0.44, "completion": 0.36, "early": 0.20},
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
    summary["folds"] = fold_records
    summary["selection_key"] = list(selection_key(result))

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
    result["plans"].to_csv(args.output / "eligible_plans.csv", index=False)
    result["orders"].to_csv(args.output / "selected_orders.csv", index=False)
    result["trades"].to_csv(args.output / "closed_trades.csv", index=False)
    search.head(250).to_csv(args.output / "top_search.csv", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
