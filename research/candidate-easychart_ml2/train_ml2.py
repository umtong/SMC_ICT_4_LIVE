#!/usr/bin/env python3
"""Train CatBoost on frozen causal plans and export the ML2 selector artifact."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


HERE = Path(__file__).resolve().parent
ML1 = HERE.parent / "candidate-easychart_ml1"
for candidate in (HERE, ML1):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from ml1_features import FEATURE_CLIP_RANGES, FEATURE_DEFAULTS, FEATURE_NAMES  # noqa: E402
from train_ml1 import _calibrate, _date_range, _logit, _split_and_purge  # noqa: E402
from ml2_model import MODEL_SCHEMA, CatBoostProbabilityModel, sha256_file  # noqa: E402


TRAINING_POLICY = (
    "CHRONOLOGICAL_TRAIN_CALIBRATION_TEST; PURGE_LABEL_INTERVALS_CROSSING_NEXT_SPLIT; "
    "CATBOOST_FITS_ONLY_TRAIN; PLATT_USES_ONLY_DISJOINT_CALIBRATION; "
    "SYMBOL_ID_EXCLUDED; SAME_EVENT_CANDIDATES_SHARE_UNIT_WEIGHT; "
    "NO_TUNED_CONFIDENCE_COVERAGE_OR_TARGET_WIN_RATE_GATE"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model-output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--train-fraction", type=float, default=0.60)
    parser.add_argument("--calibration-fraction", type=float, default=0.20)
    parser.add_argument("--embargo-minutes", type=int, default=60)
    parser.add_argument("--minimum-samples", type=int, default=300)
    parser.add_argument("--minimum-split-samples", type=int, default=60)
    parser.add_argument("--iterations", type=int, default=700)
    parser.add_argument("--depth", type=int, default=6)
    parser.add_argument("--learning-rate", type=float, default=0.03)
    parser.add_argument("--l2-leaf-reg", type=float, default=8.0)
    parser.add_argument("--random-strength", type=float, default=0.5)
    parser.add_argument("--subsample", type=float, default=0.85)
    parser.add_argument("--random-seed", type=int, default=1729)
    parser.add_argument("--risk-fraction", type=float, default=0.03)
    return parser.parse_args()


def _prediction(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    return {
        "rows": int(len(y)),
        "target_first_rate": float(y.mean()),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "roc_auc": None if len(np.unique(y)) < 2 else float(roc_auc_score(y, p)),
    }


def _selection(frame: pd.DataFrame, p: np.ndarray, risk_fraction: float) -> dict[str, Any]:
    win_r = pd.to_numeric(frame["ml_win_net_r"], errors="coerce").to_numpy(float)
    loss_r = pd.to_numeric(frame["ml_loss_net_r"], errors="coerce").to_numpy(float)
    actual = pd.to_numeric(
        frame["counterfactual_net_r_conservative"], errors="coerce"
    ).to_numpy(float)
    win_multiplier = 1.0 + risk_fraction * win_r
    loss_multiplier = 1.0 + risk_fraction * loss_r
    valid = (win_multiplier > 0.0) & (loss_multiplier > 0.0)
    expected_log = np.full(len(frame), -np.inf)
    expected_log[valid] = (
        p[valid] * np.log(win_multiplier[valid])
        + (1.0 - p[valid]) * np.log(loss_multiplier[valid])
    )
    chosen = expected_log > 0.0
    realized = actual[chosen]
    realized = realized[np.isfinite(realized)]
    labels = frame["label"].to_numpy(float)[chosen]
    days = max(1, frame["event_date"].nunique())
    return {
        "policy": "positive_expected_log_nav_growth_at_fixed_risk",
        "risk_fraction": risk_fraction,
        "selected": int(chosen.sum()),
        "coverage": float(chosen.mean()),
        "target_first_rate": None if not chosen.any() else float(labels.mean()),
        "selected_per_calendar_day": float(chosen.sum() / days),
        "mean_model_expected_log_growth": None
        if not chosen.any()
        else float(expected_log[chosen].mean()),
        "sum_observed_counterfactual_net_r": float(realized.sum()) if len(realized) else 0.0,
        "mean_observed_counterfactual_net_r": None
        if not len(realized)
        else float(realized.mean()),
    }


def train(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if not 0.0 < args.risk_fraction < 1.0:
        raise ValueError("risk_fraction must be within (0, 1)")
    frame = pd.read_csv(args.dataset, low_memory=False)
    frame = frame[frame["label"].notna()].copy()
    frame = frame.sort_values(
        ["event_time_ns", "symbol", "plan_id"], kind="mergesort"
    ).drop_duplicates(["event_time_ns", "symbol", "plan_id"], keep="last")
    if len(frame) < args.minimum_samples:
        raise RuntimeError(
            f"dataset has {len(frame)} resolved rows; need {args.minimum_samples}"
        )
    required = {
        "plan_id", "symbol", "family", "event_time_ns", "label_end_ns", "label",
        "event_date", "counterfactual_net_r_conservative", "ml_win_net_r", "ml_loss_net_r",
        *(f"mlf_{name}" for name in FEATURE_NAMES),
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"dataset missing required columns: {missing[:20]}")

    train_frame, calibration_frame, test_frame, split_report = _split_and_purge(
        frame,
        train_fraction=args.train_fraction,
        calibration_fraction=args.calibration_fraction,
        embargo_minutes=args.embargo_minutes,
    )
    for name, split in (("train", train_frame), ("calibration", calibration_frame), ("test", test_frame)):
        if len(split) < args.minimum_split_samples:
            raise RuntimeError(
                f"{name} split has {len(split)} rows after purge; need {args.minimum_split_samples}"
            )
        if split["label"].nunique() < 2:
            raise RuntimeError(f"{name} split contains one label class")

    feature_columns = [f"mlf_{name}" for name in FEATURE_NAMES]
    for split in (train_frame, calibration_frame, test_frame):
        for column in feature_columns:
            name = column.removeprefix("mlf_")
            lower, upper = FEATURE_CLIP_RANGES[name]
            split[column] = (
                pd.to_numeric(split[column], errors="coerce")
                .fillna(FEATURE_DEFAULTS[name])
                .clip(lower=lower, upper=upper)
            )

    bucket_count = train_frame.groupby("event_time_ns")["plan_id"].transform("count").to_numpy(float)
    weights = 1.0 / np.maximum(bucket_count, 1.0)
    classifier = CatBoostClassifier(
        loss_function="Logloss",
        eval_metric="Logloss",
        iterations=args.iterations,
        depth=args.depth,
        learning_rate=args.learning_rate,
        l2_leaf_reg=args.l2_leaf_reg,
        random_strength=args.random_strength,
        bootstrap_type="Bernoulli",
        subsample=args.subsample,
        random_seed=args.random_seed,
        thread_count=-1,
        verbose=False,
        allow_writing_files=False,
    )
    classifier.fit(
        train_frame[feature_columns],
        train_frame["label"].astype(int),
        sample_weight=weights,
        verbose=False,
    )

    raw_cal = classifier.predict_proba(calibration_frame[feature_columns])[:, 1]
    calibrator = LogisticRegression(C=1.0, solver="lbfgs", random_state=args.random_seed)
    calibrator.fit(
        _logit(raw_cal).reshape(-1, 1),
        calibration_frame["label"].astype(int).to_numpy(),
    )
    coefficient = float(calibrator.coef_[0, 0])
    intercept = float(calibrator.intercept_[0])

    split_frames = {"train": train_frame, "calibration": calibration_frame, "test": test_frame}
    raw_probabilities = {
        name: classifier.predict_proba(split[feature_columns])[:, 1]
        for name, split in split_frames.items()
    }
    probabilities = {
        name: _calibrate(raw, coefficient, intercept)
        for name, raw in raw_probabilities.items()
    }

    for path in (args.model_output, args.metadata_output, args.report_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    classifier.save_model(str(args.model_output), format="cbm")
    importance = sorted(
        (
            {"feature": name, "importance": float(value)}
            for name, value in zip(FEATURE_NAMES, classifier.get_feature_importance(), strict=True)
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )
    metadata: dict[str, Any] = {
        "schema": MODEL_SCHEMA,
        "status": "trained",
        "model_id": "pending",
        "model_file": os.path.relpath(args.model_output, args.metadata_output.parent),
        "model_sha256": sha256_file(args.model_output),
        "feature_names": list(FEATURE_NAMES),
        "feature_defaults": dict(FEATURE_DEFAULTS),
        "feature_clip_ranges": {name: list(FEATURE_CLIP_RANGES[name]) for name in FEATURE_NAMES},
        "risk_fraction": args.risk_fraction,
        "calibration": {"kind": "platt_logit", "coefficient": coefficient, "intercept": intercept},
        "decision": {
            "kind": "positive_expected_log_nav_growth",
            "simultaneous_rank": "expected_log_nav_growth_descending",
        },
        "training": {
            "policy": TRAINING_POLICY,
            "dataset": str(args.dataset),
            "dataset_sha256": sha256_file(args.dataset),
            "rows": int(len(frame)),
            "feature_count": len(FEATURE_NAMES),
            "symbol_identity_feature": False,
            "train_range": _date_range(train_frame),
            "calibration_range": _date_range(calibration_frame),
            "test_range": _date_range(test_frame),
            "split": split_report,
            "estimator": {
                "type": "CatBoostClassifier",
                "iterations": args.iterations,
                "depth": args.depth,
                "learning_rate": args.learning_rate,
                "l2_leaf_reg": args.l2_leaf_reg,
                "random_strength": args.random_strength,
                "subsample": args.subsample,
                "random_seed": args.random_seed,
            },
        },
    }
    metadata["model_id"] = CatBoostProbabilityModel.stable_id(metadata)
    args.metadata_output.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = {
        "model_id": metadata["model_id"],
        "training_policy": TRAINING_POLICY,
        "rows": int(len(frame)),
        "split": split_report,
        "ranges": {name: _date_range(split) for name, split in split_frames.items()},
        "prediction": {
            name: {
                "raw": _prediction(split["label"].to_numpy(int), raw_probabilities[name]),
                "calibrated": _prediction(split["label"].to_numpy(int), probabilities[name]),
            }
            for name, split in split_frames.items()
        },
        "selection": {
            name: _selection(split, probabilities[name], args.risk_fraction)
            for name, split in split_frames.items()
        },
        "feature_importance": importance,
    }
    args.report_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metadata, report


def main() -> None:
    metadata, report = train(parse_args())
    print(json.dumps({
        "model_id": metadata["model_id"],
        "rows": report["rows"],
        "test_prediction": report["prediction"]["test"]["calibrated"],
        "test_selection": report["selection"]["test"],
        "top_features": report["feature_importance"][:20],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
