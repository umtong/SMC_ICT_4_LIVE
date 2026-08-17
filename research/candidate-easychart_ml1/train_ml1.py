#!/usr/bin/env python3
"""Train, calibrate and export the causal EasyChart ML1 probability model.

The model learns P(frozen target before frozen stop).  Training, probability
calibration and test periods are chronological and disjoint; rows whose future
label interval crosses the next split are purged.  Runtime selection uses the
candidate's own post-cost break-even expectancy only.  Training does not search
for an extra confidence floor, probability edge, target win rate or coverage
quota.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ml1_features import FEATURE_CLIP_RANGES, FEATURE_DEFAULTS, FEATURE_NAMES
from ml1_model import MODEL_SCHEMA, PortableBinaryModel


TRAINING_POLICY = (
    "CHRONOLOGICAL_TRAIN_CALIBRATION_TEST; PURGE_LABEL_INTERVALS_CROSSING_NEXT_SPLIT; "
    "PLATT_CALIBRATION_ON_DISJOINT_CALIBRATION_DATA; SYMBOL_ID_NOT_A_FEATURE; "
    "NO_TUNED_CONFIDENCE_OR_COVERAGE_GATE"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _logit(probabilities: np.ndarray) -> np.ndarray:
    clipped = np.clip(probabilities.astype(float), 1e-6, 1.0 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


def _calibrate(raw: np.ndarray, coefficient: float, intercept: float) -> np.ndarray:
    values = coefficient * _logit(raw) + intercept
    values = np.clip(values, -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-values))


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    return None if len(np.unique(y)) < 2 else float(roc_auc_score(y, p))


def _reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict[str, Any]]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    output: list[dict[str, Any]] = []
    for index in range(bins):
        lower = edges[index]
        upper = edges[index + 1]
        mask = (p >= lower) & (p < upper if index < bins - 1 else p <= upper)
        if not mask.any():
            continue
        output.append(
            {
                "lower": float(lower),
                "upper": float(upper),
                "count": int(mask.sum()),
                "mean_probability": float(p[mask].mean()),
                "target_first_rate": float(y[mask].mean()),
            }
        )
    return output


def _prediction_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    return {
        "rows": int(len(y)),
        "target_first_rate": float(y.mean()),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "roc_auc": _safe_auc(y, p),
        "reliability": _reliability(y, p),
    }


def _selection_metrics(frame: pd.DataFrame, probabilities: np.ndarray) -> dict[str, Any]:
    win_r = pd.to_numeric(frame["ml_win_net_r"], errors="coerce").to_numpy(float)
    loss_r = pd.to_numeric(frame["ml_loss_net_r"], errors="coerce").to_numpy(float)
    break_even = pd.to_numeric(
        frame["ml_break_even_probability"],
        errors="coerce",
    ).to_numpy(float)
    realized = pd.to_numeric(
        frame["counterfactual_net_r_conservative"],
        errors="coerce",
    ).to_numpy(float)
    expected = probabilities * win_r + (1.0 - probabilities) * loss_r
    selected = expected > 0.0
    chosen = realized[selected]
    labels = frame["label"].to_numpy(float)[selected]
    calendar_days = max(1, frame["event_date"].nunique())
    return {
        "policy": "positive_post_cost_expected_r",
        "selected": int(selected.sum()),
        "coverage": float(selected.mean()),
        "target_first_rate": None if not selected.any() else float(labels.mean()),
        "sum_observed_counterfactual_net_r": float(chosen.sum()) if len(chosen) else 0.0,
        "mean_observed_counterfactual_net_r": None if not len(chosen) else float(chosen.mean()),
        "selected_per_calendar_day": float(selected.sum() / calendar_days),
        "mean_model_expected_net_r": None if not selected.any() else float(expected[selected].mean()),
        "mean_probability_minus_break_even": None
        if not selected.any()
        else float((probabilities[selected] - break_even[selected]).mean()),
    }


def _export_tree(estimator: Any, positive_index: int) -> dict[str, Any]:
    tree = estimator.tree_
    nodes: list[dict[str, Any]] = []
    for index in range(tree.node_count):
        left = int(tree.children_left[index])
        right = int(tree.children_right[index])
        if left == -1 and right == -1:
            counts = np.asarray(tree.value[index], dtype=float).reshape(-1)
            denominator = float(counts.sum())
            probability = 0.5 if denominator <= 0.0 else float(counts[positive_index] / denominator)
            nodes.append(
                {
                    "feature": -1,
                    "threshold": 0.0,
                    "left": -1,
                    "right": -1,
                    "probability": probability,
                }
            )
        else:
            nodes.append(
                {
                    "feature": int(tree.feature[index]),
                    "threshold": float(tree.threshold[index]),
                    "left": left,
                    "right": right,
                    "probability": None,
                }
            )
    return {"nodes": nodes}


def _split_and_purge(
    frame: pd.DataFrame,
    *,
    train_fraction: float,
    calibration_fraction: float,
    embargo_minutes: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    ordered = frame.sort_values(
        ["event_time_ns", "symbol", "plan_id"],
        kind="mergesort",
    ).copy()
    unique_times = np.sort(ordered["event_time_ns"].unique())
    if len(unique_times) < 10:
        raise RuntimeError("dataset has too few distinct event times")
    train_index = max(1, min(len(unique_times) - 3, int(len(unique_times) * train_fraction)))
    calibration_index = max(
        train_index + 1,
        min(
            len(unique_times) - 2,
            int(len(unique_times) * (train_fraction + calibration_fraction)),
        ),
    )
    calibration_start = int(unique_times[train_index])
    test_start = int(unique_times[calibration_index])
    raw_train = ordered[ordered["event_time_ns"] < calibration_start].copy()
    raw_calibration = ordered[
        (ordered["event_time_ns"] >= calibration_start)
        & (ordered["event_time_ns"] < test_start)
    ].copy()
    test = ordered[ordered["event_time_ns"] >= test_start].copy()
    embargo_ns = int(embargo_minutes) * 60_000_000_000
    train = raw_train[
        pd.to_numeric(raw_train["label_end_ns"], errors="coerce")
        < calibration_start - embargo_ns
    ].copy()
    calibration = raw_calibration[
        pd.to_numeric(raw_calibration["label_end_ns"], errors="coerce")
        < test_start - embargo_ns
    ].copy()
    report = {
        "calibration_start_ns": calibration_start,
        "test_start_ns": test_start,
        "embargo_minutes": embargo_minutes,
        "raw_train_rows": int(len(raw_train)),
        "purged_train_rows": int(len(raw_train) - len(train)),
        "train_rows": int(len(train)),
        "raw_calibration_rows": int(len(raw_calibration)),
        "purged_calibration_rows": int(len(raw_calibration) - len(calibration)),
        "calibration_rows": int(len(calibration)),
        "test_rows": int(len(test)),
    }
    return train, calibration, test, report


def _date_range(frame: pd.DataFrame) -> dict[str, str | None]:
    if frame.empty:
        return {"start": None, "end": None}
    timestamps = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    return {"start": timestamps.min().isoformat(), "end": timestamps.max().isoformat()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--train-fraction", type=float, default=0.60)
    parser.add_argument("--calibration-fraction", type=float, default=0.20)
    parser.add_argument("--embargo-minutes", type=int, default=60)
    parser.add_argument("--minimum-samples", type=int, default=300)
    parser.add_argument("--minimum-split-samples", type=int, default=60)
    parser.add_argument("--n-estimators", type=int, default=256)
    parser.add_argument("--max-depth", type=int, default=7)
    parser.add_argument("--min-samples-leaf", type=int, default=24)
    parser.add_argument("--random-state", type=int, default=1729)
    return parser.parse_args()


def train(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    frame = pd.read_csv(args.dataset, low_memory=False)
    frame = frame[frame["label"].notna()].copy()
    if len(frame) < args.minimum_samples:
        raise RuntimeError(
            f"dataset has {len(frame)} resolved rows; need at least {args.minimum_samples}",
        )
    if not 0.0 < args.train_fraction < 1.0:
        raise ValueError("train_fraction must be within (0, 1)")
    if not 0.0 < args.calibration_fraction < 1.0:
        raise ValueError("calibration_fraction must be within (0, 1)")
    if args.train_fraction + args.calibration_fraction >= 1.0:
        raise ValueError("train + calibration fractions must leave a test segment")

    required_columns = {
        "plan_id",
        "symbol",
        "event_time_ns",
        "label_end_ns",
        "label",
        "event_date",
        "counterfactual_net_r_conservative",
        "ml_win_net_r",
        "ml_loss_net_r",
        "ml_break_even_probability",
        *(f"mlf_{name}" for name in FEATURE_NAMES),
    }
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise RuntimeError(f"dataset missing required columns: {missing[:20]}")

    train_frame, calibration_frame, test_frame, split_report = _split_and_purge(
        frame,
        train_fraction=args.train_fraction,
        calibration_fraction=args.calibration_fraction,
        embargo_minutes=args.embargo_minutes,
    )
    for name, split in (
        ("train", train_frame),
        ("calibration", calibration_frame),
        ("test", test_frame),
    ):
        if len(split) < args.minimum_split_samples:
            raise RuntimeError(
                f"{name} split has {len(split)} rows after purging; need {args.minimum_split_samples}",
            )
        if split["label"].nunique() < 2:
            raise RuntimeError(f"{name} split contains only one label class")

    feature_columns = [f"mlf_{name}" for name in FEATURE_NAMES]
    for column in feature_columns:
        feature_name = column.removeprefix("mlf_")
        frame_default = FEATURE_DEFAULTS[feature_name]
        lower, upper = FEATURE_CLIP_RANGES[feature_name]
        for split in (train_frame, calibration_frame, test_frame):
            split[column] = (
                pd.to_numeric(split[column], errors="coerce")
                .fillna(frame_default)
                .clip(lower=lower, upper=upper)
            )

    x_train = train_frame[feature_columns].to_numpy(float)
    y_train = train_frame["label"].to_numpy(int)
    bucket_count = train_frame.groupby("event_time_ns")["plan_id"].transform("count").to_numpy(float)
    sample_weight = 1.0 / np.maximum(bucket_count, 1.0)

    classifier = ExtraTreesClassifier(
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        min_samples_leaf=args.min_samples_leaf,
        max_features="sqrt",
        bootstrap=False,
        class_weight=None,
        random_state=args.random_state,
        n_jobs=-1,
    )
    classifier.fit(x_train, y_train, sample_weight=sample_weight)
    positive_index = int(np.flatnonzero(classifier.classes_ == 1)[0])

    raw_calibration = classifier.predict_proba(
        calibration_frame[feature_columns].to_numpy(float),
    )[:, positive_index]
    calibrator = LogisticRegression(C=1.0, solver="lbfgs", random_state=args.random_state)
    calibrator.fit(
        _logit(raw_calibration).reshape(-1, 1),
        calibration_frame["label"].to_numpy(int),
    )
    coefficient = float(calibrator.coef_[0, 0])
    intercept = float(calibrator.intercept_[0])
    calibrated_calibration = _calibrate(raw_calibration, coefficient, intercept)

    raw_train = classifier.predict_proba(x_train)[:, positive_index]
    raw_test = classifier.predict_proba(
        test_frame[feature_columns].to_numpy(float),
    )[:, positive_index]
    calibrated_train = _calibrate(raw_train, coefficient, intercept)
    calibrated_test = _calibrate(raw_test, coefficient, intercept)

    importance = sorted(
        (
            {"feature": name, "importance": float(value)}
            for name, value in zip(FEATURE_NAMES, classifier.feature_importances_, strict=True)
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )
    model: dict[str, Any] = {
        "schema": MODEL_SCHEMA,
        "model_type": "extra_trees_binary",
        "status": "trained",
        "model_id": "pending",
        "feature_names": list(FEATURE_NAMES),
        "feature_defaults": dict(FEATURE_DEFAULTS),
        "feature_clip_ranges": {
            name: list(FEATURE_CLIP_RANGES[name])
            for name in FEATURE_NAMES
        },
        "trees": [
            _export_tree(estimator, positive_index)
            for estimator in classifier.estimators_
        ],
        "calibration": {
            "kind": "platt_logit",
            "coefficient": coefficient,
            "intercept": intercept,
        },
        "decision": {"kind": "positive_post_cost_expectancy"},
        "training": {
            "policy": TRAINING_POLICY,
            "dataset": str(args.dataset),
            "dataset_sha256": _sha256(args.dataset),
            "feature_count": len(FEATURE_NAMES),
            "rows": int(len(frame)),
            "train_range": _date_range(train_frame),
            "calibration_range": _date_range(calibration_frame),
            "test_range": _date_range(test_frame),
            "split": split_report,
            "estimator": {
                "type": "ExtraTreesClassifier",
                "n_estimators": args.n_estimators,
                "max_depth": args.max_depth,
                "min_samples_leaf": args.min_samples_leaf,
                "max_features": "sqrt",
                "random_state": args.random_state,
            },
        },
    }
    model["model_id"] = PortableBinaryModel.stable_id(model)

    portable = PortableBinaryModel(model)
    parity_rows = min(100, len(test_frame))
    portable_probabilities = np.array(
        [
            portable.probability(
                {
                    name: float(test_frame.iloc[index][f"mlf_{name}"])
                    for name in FEATURE_NAMES
                },
            )
            for index in range(parity_rows)
        ],
    )
    max_parity_error = float(
        np.max(np.abs(portable_probabilities - calibrated_test[:parity_rows])),
    )
    if max_parity_error > 1e-10:
        raise RuntimeError(f"portable model parity error {max_parity_error}")

    report: dict[str, Any] = {
        "model_id": model["model_id"],
        "training_policy": TRAINING_POLICY,
        "dataset": str(args.dataset),
        "dataset_sha256": _sha256(args.dataset),
        "split": split_report,
        "ranges": {
            "train": _date_range(train_frame),
            "calibration": _date_range(calibration_frame),
            "test": _date_range(test_frame),
        },
        "prediction": {
            "train": _prediction_metrics(train_frame["label"].to_numpy(int), calibrated_train),
            "calibration": _prediction_metrics(
                calibration_frame["label"].to_numpy(int),
                calibrated_calibration,
            ),
            "test": _prediction_metrics(test_frame["label"].to_numpy(int), calibrated_test),
        },
        "selection": {
            "calibration": _selection_metrics(calibration_frame, calibrated_calibration),
            "test": _selection_metrics(test_frame, calibrated_test),
        },
        "top_feature_importance": importance[:30],
        "portable_probability_max_abs_error": max_parity_error,
        "notes": (
            "Selection diagnostics use positive model post-cost expectancy without a tuned "
            "confidence margin. Final performance still comes from the four-symbol, one-slot "
            "continuous NautilusTrader account."
        ),
    }
    return model, report


def main() -> None:
    args = parse_args()
    model, report = train(args)
    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    args.model_output.write_text(
        json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path = args.report_output or args.model_output.with_suffix(".report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
