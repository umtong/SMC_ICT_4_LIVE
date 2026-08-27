#!/usr/bin/env python3
"""Fit and freeze the symbol-agnostic first-response probability model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import liquidity_control_v2 as policy  # noqa: E402

NUMERIC_FEATURES = [
    "planned_target_net_r",
    "risk_bps",
    "departure_common_return_3m_signed",
    "departure_common_breadth_3m_signed",
    "departure_residual_return_1m_signed",
    "departure_residual_return_15m_signed",
    "confirmation_common_return_5m_signed",
    "confirmation_activity_ratio",
    "confirmation_trade_size_ratio",
    "approach_impact_per_activity_12m",
    "approach_path_efficiency",
    "sequence_block_0_return_bps_signed",
    "sequence_block_1_return_bps_signed",
    "sequence_block_2_return_bps_signed",
    "sequence_block_5_return_bps_signed",
    "sequence_block_5_impact_efficiency_signed",
    "event_impact_per_activity",
    "event_delta_share_signed",
    "source_strength_ratio",
    "source_semantic_weight",
    "source_log_scale",
    "target_log_scale",
    "role_align_15",
    "role_align_60",
    "role_align_240",
    "clock_hour_sin2",
    "clock_hour_cos2",
]
CATEGORICAL_FEATURES = ["location_kind", "auction_phase", "family", "setup_kind"]
REGULARIZATION_C = 0.1


def make_model() -> Pipeline:
    preprocessing = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                NUMERIC_FEATURES,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("one_hot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                CATEGORICAL_FEATURES,
            ),
        ]
    )
    return Pipeline(
        [
            ("preprocess", preprocessing),
            (
                "model",
                LogisticRegression(
                    C=REGULARIZATION_C,
                    max_iter=3000,
                    solver="liblinear",
                    random_state=1729,
                ),
            ),
        ]
    )


def export_model(model: Pipeline) -> dict:
    preprocessing = model.named_steps["preprocess"]
    numeric = preprocessing.named_transformers_["num"]
    categorical = preprocessing.named_transformers_["cat"]
    numeric_imputer = numeric.named_steps["impute"]
    scaler = numeric.named_steps["scale"]
    categorical_imputer = categorical.named_steps["impute"]
    one_hot = categorical.named_steps["one_hot"]
    estimator = model.named_steps["model"]
    return {
        "numeric_features": NUMERIC_FEATURES,
        "numeric_medians": [float(value) for value in numeric_imputer.statistics_],
        "numeric_means": [float(value) for value in scaler.mean_],
        "numeric_scales": [
            float(value) if abs(float(value)) > 1e-15 else 1.0 for value in scaler.scale_
        ],
        "categorical_features": CATEGORICAL_FEATURES,
        "categorical_fill_values": [
            str(value) for value in categorical_imputer.statistics_
        ],
        "categorical_categories": [
            [str(value) for value in values] for values in one_hot.categories_
        ],
        "coefficients": [float(value) for value in estimator.coef_[0]],
        "intercept": float(estimator.intercept_[0]),
    }


def fit(root: Path, output: Path, periods: list[str]) -> dict:
    source = policy.load_actions(root)
    source = source[source["research_period"].astype(str).isin(periods)].copy()
    states = policy.engineer(
        policy.first_episode_state(policy.choose_geometry(source))
    )
    if not len(states):
        raise RuntimeError("no first-response states")
    outcomes = policy.s(states, "outcome")
    filled = outcomes.ne("UNFILLED").astype(int)
    target = outcomes.eq("TARGET_FIRST").astype(int)

    fill_model = make_model()
    fill_model.fit(states, filled)
    win_model = make_model()
    win_model.fit(states.loc[filled.eq(1)], target.loc[filled.eq(1)])

    result = {
        "policy": policy.POLICY,
        "training_periods": periods,
        "training_first_episode_states": int(len(states)),
        "training_filled_first_states": int(filled.sum()),
        "training_target_first": int(target.sum()),
        "risk_fraction": policy.RISK,
        "minimum_planned_target_net_r": policy.MIN_R,
        "maximum_realized_target_net_r": policy.CAP_R,
        "selection_expected_net_r_threshold": 0.0,
        "regularization_C": REGULARIZATION_C,
        "first_state_only": True,
        "decision_uses_symbol_identity": False,
        "decision_uses_outcome_fields": False,
        "fill_model": export_model(fill_model),
        "win_model": export_model(win_model),
    }
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--period", action="append", required=True)
    args = parser.parse_args()
    fit(args.root, args.output, args.period)


if __name__ == "__main__":
    main()
