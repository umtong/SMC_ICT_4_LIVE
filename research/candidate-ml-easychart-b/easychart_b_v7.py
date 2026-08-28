#!/usr/bin/env python3
"""V7 nonlinear expert-distilled causal trigger with structural targets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

import easychart_b_v4 as base
import easychart_b_v6 as expert

POLICY_NAME = "ML_EASYCHART_B_V7_NONLINEAR_CAUSAL_TRIGGER_NATURAL_STRUCTURE"


def numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    return base.numeric(frame, column, default)


def text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    return base.text(frame, column, default)


def raw_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return expert.raw_feature_frame(frame)


def _positive_probability(model: Any, matrix: np.ndarray) -> np.ndarray:
    classes = list(model.classes_)
    probabilities = model.predict_proba(matrix)
    if 1.0 in classes:
        return probabilities[:, classes.index(1.0)]
    if 1 in classes:
        return probabilities[:, classes.index(1)]
    return np.full(len(matrix), float(classes[0] > 0), dtype=float)


def score_actions(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    models: dict[str, Any],
) -> pd.DataFrame:
    out = expert.attach_experts(frame)
    features = raw_feature_frame(out).reindex(columns=policy["feature_names"])
    matrix = features.to_numpy(float)
    base_probability = _positive_probability(models["base_excursion"], matrix)
    completion_probability = _positive_probability(
        models["structural_completion"], matrix
    )
    timing_probability = _positive_probability(models["earliest_good_arm"], matrix)
    out["base_excursion_probability"] = base_probability
    out["structural_completion_probability"] = completion_probability
    out["earliest_good_arm_probability"] = timing_probability
    weights = policy.get(
        "score_weights", {"base": 0.46, "completion": 0.34, "early": 0.20}
    )
    epsilon = 1e-9
    blended_logit = np.zeros(len(out), dtype=float)
    for probability, key in zip(
        (base_probability, completion_probability, timing_probability),
        ("base", "completion", "early"),
        strict=True,
    ):
        probability = np.clip(probability, epsilon, 1.0 - epsilon)
        blended_logit += float(weights[key]) * np.log(
            probability / (1.0 - probability)
        )
    out["trigger_score"] = 1.0 / (
        1.0 + np.exp(-np.clip(blended_logit, -35.0, 35.0))
    )
    return out


def select_plans(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    models: dict[str, Any],
    *,
    pre_scored: bool = False,
) -> pd.DataFrame:
    scored = frame.copy() if pre_scored else score_actions(frame, policy, models)
    return expert.select_plans(
        scored,
        {"selection": policy["selection"]},
        pre_scored=True,
    )


def route_continuous_account(
    plans: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return expert.route_continuous_account(plans)


def build_summary(
    source: pd.DataFrame,
    selected: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    bounds: dict[str, dict[str, str]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    summary = expert.build_summary(
        source,
        selected,
        orders,
        trades,
        bounds,
        {
            "selection": policy["selection"],
            "score_weights": policy.get("score_weights"),
        },
    )
    summary.update(
        {
            "policy": POLICY_NAME,
            "model_family": (
                "sklearn HistGradientBoostingClassifier, three independent causal heads"
            ),
            "fixed_r_target_cap": False,
            "target_contract": "nearest still-live causal market structure",
            "runtime_uses_symbol_identity": False,
            "runtime_uses_calendar_identity": False,
            "runtime_uses_outcome_fields": False,
        }
    )
    return summary


def run(
    root: Path,
    bounds_path: Path,
    policy_path: Path,
    models_path: Path,
    output: Path,
) -> dict[str, Any]:
    bounds = json.loads(bounds_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    models = joblib.load(models_path)
    actions = base.load_actions(root, bounds)
    scored = score_actions(actions, policy, models)
    selected = select_plans(scored, policy, models, pre_scored=True)
    orders, trades = route_continuous_account(selected)
    summary = build_summary(actions, selected, orders, trades, bounds, policy)
    output.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output / "eligible_plans.csv", index=False)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period-bounds", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.period_bounds, args.policy, args.models, args.output)


if __name__ == "__main__":
    main()
