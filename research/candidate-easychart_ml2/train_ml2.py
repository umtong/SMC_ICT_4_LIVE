#!/usr/bin/env python3
"""Train the EasyChart ML2 target-before-stop probability model.

The learning target is the first passage of the immutable target versus the
immutable stop.  CatBoost minimizes probabilistic log loss; a separate later
chronological segment calibrates probabilities with Platt scaling.  Neither a
requested win rate, a trade-frequency target nor any hand-picked example is a
training objective.  Fixed-risk expected log NAV growth is used only after the
probability estimate, when a complete plan must be selected or rejected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ml2_features import FEATURE_CLIP_RANGES, FEATURE_DEFAULTS, FEATURE_NAMES
from ml2_model import MODEL_SCHEMA, CatBoostProbabilityModel, sha256_file


TRAINING_POLICY = (
    "CHRONOLOGICAL_TRAIN_CALIBRATION_TEST_BY_COMPLETE_DECISION_TIME; "
    "PURGE_LABEL_INTERVALS_CROSSING_THE_NEXT_SPLIT_WITH_EMBARGO; "
    "CATBOOST_FITS_ONLY_TRAIN_TARGET_FIRST_LOGLOSS; "
    "PLATT_FITS_ONLY_DISJOINT_CALIBRATION; SYMBOL_ID_EXCLUDED; "
    "EACH_SYMBOL_NAMESPACED_CAUSAL_EVENT_HAS_UNIT_TOTAL_WEIGHT; "
    "NO_TARGET_WIN_RATE_TRADE_FREQUENCY_OR_USER_EXAMPLE_OBJECTIVE; "
    "NO_TUNED_RUNTIME_CONFIDENCE_THRESHOLD"
)
SELECTION_DIAGNOSTIC_POLICY = (
    "POST_MODEL_ONLY:POSITIVE_EXPECTED_LOG_NAV_GROWTH_AT_FIXED_RISK; "
    "SAME_COMPLETED_DECISION_BUCKET_RANKS_BY_EXPECTED_LOG_GROWTH; "
    "OFFLINE_DIAGNOSTIC_DOES_NOT_PRETEND_TO_RESOLVE_LATER_GLOBAL_SLOT_CONFLICTS"
)
_EPS = 1e-9


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
    parser.add_argument("--iterations", type=int, default=800)
    parser.add_argument("--depth", type=int, default=6)
    parser.add_argument("--learning-rate", type=float, default=0.03)
    parser.add_argument("--l2-leaf-reg", type=float, default=10.0)
    parser.add_argument("--random-strength", type=float, default=0.5)
    parser.add_argument("--subsample", type=float, default=0.85)
    parser.add_argument("--random-seed", type=int, default=1729)
    parser.add_argument("--risk-fraction", type=float, default=0.03)
    return parser.parse_args()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clip_probability(values: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=float), _EPS, 1.0 - _EPS)


def _logit(values: np.ndarray) -> np.ndarray:
    probabilities = _clip_probability(values)
    return np.log(probabilities / (1.0 - probabilities))


def _calibrate(raw: np.ndarray, coefficient: float, intercept: float) -> np.ndarray:
    linear = np.clip(coefficient * _logit(raw) + intercept, -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-linear))


def _event_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("event_group_id", sort=False)["plan_id"].transform("count")
    weights = 1.0 / np.maximum(counts.to_numpy(float), 1.0)
    # Keep the average weight at one so regularization magnitudes remain easy to
    # interpret while every causal event still has equal total influence.
    return weights * (len(weights) / max(weights.sum(), _EPS))


def _date_range(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"start": None, "end": None, "rows": 0, "causal_events": 0}
    timestamps = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    return {
        "start": timestamps.min().isoformat(),
        "end": timestamps.max().isoformat(),
        "rows": int(len(frame)),
        "causal_events": int(frame["event_group_id"].nunique()),
    }


def _split_and_purge(
    frame: pd.DataFrame,
    *,
    train_fraction: float,
    calibration_fraction: float,
    embargo_minutes: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be within (0, 1)")
    if not 0.0 < calibration_fraction < 1.0:
        raise ValueError("calibration_fraction must be within (0, 1)")
    if train_fraction + calibration_fraction >= 1.0:
        raise ValueError("train_fraction + calibration_fraction must be below 1")
    if embargo_minutes < 0:
        raise ValueError("embargo_minutes cannot be negative")

    ordered = frame.sort_values(
        ["event_time_ns", "symbol", "plan_id"],
        kind="mergesort",
    ).copy()
    unique_times = np.sort(ordered["event_time_ns"].unique())
    if len(unique_times) < 3:
        raise RuntimeError("dataset needs at least three distinct decision times")
    train_index = min(
        len(unique_times) - 2,
        max(1, int(math.floor(len(unique_times) * train_fraction))),
    )
    calibration_end_index = min(
        len(unique_times) - 1,
        max(
            train_index + 1,
            int(math.floor(len(unique_times) * (train_fraction + calibration_fraction))),
        ),
    )
    calibration_start_ns = int(unique_times[train_index])
    test_start_ns = int(unique_times[calibration_end_index])

    raw_train = ordered[ordered["event_time_ns"] < calibration_start_ns].copy()
    raw_calibration = ordered[
        (ordered["event_time_ns"] >= calibration_start_ns)
        & (ordered["event_time_ns"] < test_start_ns)
    ].copy()
    test = ordered[ordered["event_time_ns"] >= test_start_ns].copy()

    embargo_ns = int(embargo_minutes) * 60_000_000_000
    train_label_cutoff = calibration_start_ns - embargo_ns
    calibration_label_cutoff = test_start_ns - embargo_ns
    train = raw_train[
        pd.to_numeric(raw_train["label_end_ns"], errors="coerce") < train_label_cutoff
    ].copy()
    calibration = raw_calibration[
        pd.to_numeric(raw_calibration["label_end_ns"], errors="coerce")
        < calibration_label_cutoff
    ].copy()

    report = {
        "calibration_start_ns": calibration_start_ns,
        "calibration_start": pd.Timestamp(calibration_start_ns, unit="ns", tz="UTC").isoformat(),
        "test_start_ns": test_start_ns,
        "test_start": pd.Timestamp(test_start_ns, unit="ns", tz="UTC").isoformat(),
        "embargo_minutes": int(embargo_minutes),
        "raw_rows": {
            "train": int(len(raw_train)),
            "calibration": int(len(raw_calibration)),
            "test": int(len(test)),
        },
        "purged_rows": {
            "train": int(len(raw_train) - len(train)),
            "calibration": int(len(raw_calibration) - len(calibration)),
        },
        "final_rows": {
            "train": int(len(train)),
            "calibration": int(len(calibration)),
            "test": int(len(test)),
        },
    }
    return train, calibration, test, report


def _prepare_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for name in FEATURE_NAMES:
        column = f"ml2f_{name}"
        lower, upper = FEATURE_CLIP_RANGES[name]
        output[column] = (
            pd.to_numeric(output[column], errors="coerce")
            .fillna(FEATURE_DEFAULTS[name])
            .clip(lower=lower, upper=upper)
        )
    return output


def _prediction_metrics(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
) -> dict[str, Any]:
    y = frame["label"].to_numpy(int)
    p = _clip_probability(probabilities)
    weights = _event_weights(frame)
    auc = None if len(np.unique(y)) < 2 else float(roc_auc_score(y, p, sample_weight=weights))
    return {
        "rows": int(len(frame)),
        "causal_events": int(frame["event_group_id"].nunique()),
        "target_first_rate": float(y.mean()),
        "event_weighted_target_first_rate": float(np.average(y, weights=weights)),
        "brier": float(brier_score_loss(y, p)),
        "event_weighted_brier": float(brier_score_loss(y, p, sample_weight=weights)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "event_weighted_log_loss": float(
            log_loss(y, p, labels=[0, 1], sample_weight=weights),
        ),
        "event_weighted_roc_auc": auc,
        "mean_probability": float(p.mean()),
        "event_weighted_mean_probability": float(np.average(p, weights=weights)),
    }


def _expected_utility(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    risk_fraction: float,
) -> tuple[np.ndarray, np.ndarray]:
    p = _clip_probability(probabilities)
    win_r = pd.to_numeric(frame["ml2_win_net_r"], errors="coerce").to_numpy(float)
    loss_r = pd.to_numeric(frame["ml2_loss_net_r"], errors="coerce").to_numpy(float)
    win_multiplier = 1.0 + risk_fraction * win_r
    loss_multiplier = 1.0 + risk_fraction * loss_r
    valid = (
        np.isfinite(win_r)
        & np.isfinite(loss_r)
        & (win_r > 0.0)
        & (loss_r < 0.0)
        & (win_multiplier > 0.0)
        & (loss_multiplier > 0.0)
    )
    expected_log = np.full(len(frame), -np.inf, dtype=float)
    required = np.ones(len(frame), dtype=float)
    if valid.any():
        win_log = np.log(win_multiplier[valid])
        loss_log = np.log(loss_multiplier[valid])
        denominator = win_log - loss_log
        required[valid] = np.clip(-loss_log / denominator, 0.0, 1.0)
        expected_log[valid] = p[valid] * win_log + (1.0 - p[valid]) * loss_log
    return expected_log, required


def _selection_summary(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    *,
    risk_fraction: float,
    arbitrate_same_bucket: bool,
) -> dict[str, Any]:
    expected_log, required = _expected_utility(frame, probabilities, risk_fraction)
    working = frame.copy()
    working["_probability"] = probabilities
    working["_expected_log"] = expected_log
    working["_required_probability"] = required
    working["_selectable"] = np.isfinite(expected_log) & (expected_log > 0.0)

    if arbitrate_same_bucket:
        selectable = working[working["_selectable"]].copy()
        selected = (
            selectable.sort_values(
                [
                    "decision_bucket_id",
                    "_expected_log",
                    "_probability",
                    "event_time_ns",
                    "symbol",
                    "plan_id",
                ],
                ascending=[True, False, False, True, True, True],
                kind="mergesort",
            )
            .groupby("decision_bucket_id", sort=False, as_index=False)
            .head(1)
        )
        policy = "highest_positive_expected_log_per_completed_decision_bucket"
    else:
        selected = working[working["_selectable"]].copy()
        policy = "all_positive_expected_log_counterfactual_candidates"

    observed = pd.to_numeric(selected["observed_outcome_net_r"], errors="coerce")
    valid_observed = np.isfinite(observed.to_numpy(float))
    actual_multiplier = 1.0 + risk_fraction * observed.to_numpy(float)
    valid_multiplier = valid_observed & (actual_multiplier > 0.0)
    realized_log = np.log(actual_multiplier[valid_multiplier]) if valid_multiplier.any() else np.array([])
    resolution = pd.to_numeric(
        selected.get("counterfactual_minutes_to_resolution"),
        errors="coerce",
    )
    days = max(1, working["event_date"].nunique())
    return {
        "policy": policy,
        "policy_scope": (
            "counterfactual selection diagnostic; the continuous Nautilus account remains "
            "the authority for active-position conflicts and NAV"
        ),
        "risk_fraction": risk_fraction,
        "candidate_rows": int(len(working)),
        "selectable_rows": int(working["_selectable"].sum()),
        "selected_rows": int(len(selected)),
        "coverage": float(len(selected) / len(working)) if len(working) else 0.0,
        "selected_per_calendar_day": float(len(selected) / days),
        "selected_target_first_rate": None
        if selected.empty
        else float(selected["label"].mean()),
        "mean_selected_probability": None
        if selected.empty
        else float(selected["_probability"].mean()),
        "mean_model_expected_log_growth": None
        if selected.empty
        else float(selected["_expected_log"].mean()),
        "mean_observed_outcome_net_r": None
        if not valid_observed.any()
        else float(observed.to_numpy(float)[valid_observed].mean()),
        "sum_observed_outcome_net_r": 0.0
        if not valid_observed.any()
        else float(observed.to_numpy(float)[valid_observed].sum()),
        "counterfactual_realized_log_nav_sum": 0.0
        if not len(realized_log)
        else float(realized_log.sum()),
        "median_minutes_to_resolution": None
        if resolution.dropna().empty
        else float(resolution.median()),
    }


def _group_prediction(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    column: str,
) -> dict[str, Any]:
    probability_series = pd.Series(probabilities, index=frame.index, dtype=float)
    output: dict[str, Any] = {}
    for key, group in frame.groupby(column, sort=True, dropna=False):
        p = probability_series.loc[group.index].to_numpy(float)
        output["<NA>" if pd.isna(key) else str(key)] = _prediction_metrics(group, p)
    return output


def _fit_platt(
    raw_probabilities: np.ndarray,
    frame: pd.DataFrame,
    random_seed: int,
) -> tuple[float, float]:
    calibrator = LogisticRegression(
        C=1.0,
        solver="lbfgs",
        random_state=random_seed,
        max_iter=1000,
    )
    calibrator.fit(
        _logit(raw_probabilities).reshape(-1, 1),
        frame["label"].astype(int).to_numpy(),
        sample_weight=_event_weights(frame),
    )
    return float(calibrator.coef_[0, 0]), float(calibrator.intercept_[0])


def train(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if not 0.0 < float(args.risk_fraction) < 1.0:
        raise ValueError("risk_fraction must be within (0, 1)")
    if int(args.iterations) <= 0 or int(args.depth) <= 0:
        raise ValueError("iterations and depth must be positive")

    frame = pd.read_csv(args.dataset, low_memory=False)
    required = {
        "plan_id",
        "event_group_id",
        "decision_bucket_id",
        "symbol",
        "family",
        "side",
        "ml2_causal_family",
        "event_time_ns",
        "label_end_ns",
        "event_date",
        "label",
        "observed_outcome_net_r",
        "ml2_win_net_r",
        "ml2_loss_net_r",
        *(f"ml2f_{name}" for name in FEATURE_NAMES),
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"dataset missing required columns: {missing[:30]}")

    frame = frame[frame["label"].notna()].copy()
    frame["label"] = pd.to_numeric(frame["label"], errors="raise").astype(int)
    if not frame["label"].isin((0, 1)).all():
        raise RuntimeError("label must be binary 0/1")
    frame["event_time_ns"] = pd.to_numeric(frame["event_time_ns"], errors="raise").astype("int64")
    frame["label_end_ns"] = pd.to_numeric(frame["label_end_ns"], errors="raise").astype("int64")
    frame = frame.sort_values(
        ["event_time_ns", "symbol", "plan_id"],
        kind="mergesort",
    )
    if frame["plan_id"].duplicated().any():
        raise RuntimeError("dataset contains duplicate plan_id values")
    if len(frame) < int(args.minimum_samples):
        raise RuntimeError(
            f"dataset has {len(frame)} resolved rows; need {args.minimum_samples}",
        )
    if frame["label"].nunique() < 2:
        raise RuntimeError("full dataset contains only one label class")
    if any("symbol" in name.lower() for name in FEATURE_NAMES):
        raise RuntimeError("symbol identity is forbidden from the ML2 feature schema")

    train_frame, calibration_frame, test_frame, split_report = _split_and_purge(
        frame,
        train_fraction=float(args.train_fraction),
        calibration_fraction=float(args.calibration_fraction),
        embargo_minutes=int(args.embargo_minutes),
    )
    for name, split in (
        ("train", train_frame),
        ("calibration", calibration_frame),
        ("test", test_frame),
    ):
        if len(split) < int(args.minimum_split_samples):
            raise RuntimeError(
                f"{name} split has {len(split)} rows after purge; "
                f"need {args.minimum_split_samples}",
            )
        if split["label"].nunique() < 2:
            raise RuntimeError(f"{name} split contains only one label class")

    train_frame = _prepare_features(train_frame)
    calibration_frame = _prepare_features(calibration_frame)
    test_frame = _prepare_features(test_frame)
    feature_columns = [f"ml2f_{name}" for name in FEATURE_NAMES]

    classifier = CatBoostClassifier(
        loss_function="Logloss",
        eval_metric="Logloss",
        iterations=int(args.iterations),
        depth=int(args.depth),
        learning_rate=float(args.learning_rate),
        l2_leaf_reg=float(args.l2_leaf_reg),
        random_strength=float(args.random_strength),
        bootstrap_type="Bernoulli",
        subsample=float(args.subsample),
        random_seed=int(args.random_seed),
        thread_count=-1,
        verbose=False,
        allow_writing_files=False,
        has_time=True,
    )
    classifier.fit(
        train_frame[feature_columns],
        train_frame["label"],
        sample_weight=_event_weights(train_frame),
        verbose=False,
    )

    split_frames = {
        "train": train_frame,
        "calibration": calibration_frame,
        "test": test_frame,
    }
    raw_probabilities = {
        name: classifier.predict_proba(split[feature_columns])[:, 1]
        for name, split in split_frames.items()
    }
    coefficient, intercept = _fit_platt(
        raw_probabilities["calibration"],
        calibration_frame,
        int(args.random_seed),
    )
    calibrated_probabilities = {
        name: _calibrate(raw, coefficient, intercept)
        for name, raw in raw_probabilities.items()
    }

    for path in (args.model_output, args.metadata_output, args.report_output):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    classifier.save_model(str(args.model_output), format="cbm")

    importance = sorted(
        (
            {"feature": name, "importance": float(value)}
            for name, value in zip(
                FEATURE_NAMES,
                classifier.get_feature_importance(),
                strict=True,
            )
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )
    metadata: dict[str, Any] = {
        "schema": MODEL_SCHEMA,
        "status": "trained",
        "model_id": "pending",
        "model_file": os.path.relpath(args.model_output, Path(args.metadata_output).parent),
        "model_sha256": sha256_file(Path(args.model_output)),
        "feature_names": list(FEATURE_NAMES),
        "feature_defaults": dict(FEATURE_DEFAULTS),
        "feature_clip_ranges": {
            name: list(FEATURE_CLIP_RANGES[name])
            for name in FEATURE_NAMES
        },
        "risk_fraction": float(args.risk_fraction),
        "calibration": {
            "kind": "platt_logit",
            "coefficient": coefficient,
            "intercept": intercept,
        },
        "decision": {
            "kind": "positive_expected_log_nav_growth",
            "simultaneous_rank": "expected_log_nav_growth_descending",
            "geometry_source": "immutable_deterministic_plan",
            "risk_sizing_controlled_by_model": False,
        },
        "training": {
            "policy": TRAINING_POLICY,
            "selection_diagnostic_policy": SELECTION_DIAGNOSTIC_POLICY,
            "dataset": str(args.dataset),
            "dataset_sha256": _hash_file(Path(args.dataset)),
            "rows": int(len(frame)),
            "causal_events": int(frame["event_group_id"].nunique()),
            "feature_count": len(FEATURE_NAMES),
            "symbol_identity_feature": False,
            "train_range": _date_range(train_frame),
            "calibration_range": _date_range(calibration_frame),
            "test_range": _date_range(test_frame),
            "split": split_report,
            "estimator": {
                "type": "CatBoostClassifier",
                "loss_function": "Logloss",
                "iterations": int(args.iterations),
                "depth": int(args.depth),
                "learning_rate": float(args.learning_rate),
                "l2_leaf_reg": float(args.l2_leaf_reg),
                "random_strength": float(args.random_strength),
                "subsample": float(args.subsample),
                "has_time": True,
                "random_seed": int(args.random_seed),
            },
        },
    }
    metadata["model_id"] = CatBoostProbabilityModel.stable_id(metadata)
    Path(args.metadata_output).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Load through the exact runtime path and compare probabilities.  This
    # catches metadata, checksum, feature-order and calibration drift before the
    # model can enter a Nautilus run.
    runtime = CatBoostProbabilityModel(Path(args.metadata_output))
    runtime.assert_selectable()
    parity_rows = min(200, len(test_frame))
    for position in range(parity_rows):
        row = test_frame.iloc[position]
        feature_map = {name: float(row[f"ml2f_{name}"]) for name in FEATURE_NAMES}
        runtime_probability = runtime.probability(feature_map)
        expected_probability = float(calibrated_probabilities["test"][position])
        if abs(runtime_probability - expected_probability) > 1e-12:
            raise RuntimeError(
                f"runtime model parity failed at test row {position}: "
                f"{runtime_probability} != {expected_probability}",
            )

    report_splits: dict[str, Any] = {}
    for name, split in split_frames.items():
        probability = calibrated_probabilities[name]
        report_splits[name] = {
            "range": _date_range(split),
            "raw_prediction": _prediction_metrics(split, raw_probabilities[name]),
            "calibrated_prediction": _prediction_metrics(split, probability),
            "all_positive_candidates": _selection_summary(
                split,
                probability,
                risk_fraction=float(args.risk_fraction),
                arbitrate_same_bucket=False,
            ),
            "same_bucket_arbitration": _selection_summary(
                split,
                probability,
                risk_fraction=float(args.risk_fraction),
                arbitrate_same_bucket=True,
            ),
            "by_causal_family": _group_prediction(split, probability, "ml2_causal_family"),
            "by_symbol": _group_prediction(split, probability, "symbol"),
        }

    report: dict[str, Any] = {
        "model_id": metadata["model_id"],
        "model_sha256": metadata["model_sha256"],
        "training_policy": TRAINING_POLICY,
        "selection_diagnostic_policy": SELECTION_DIAGNOSTIC_POLICY,
        "rows": int(len(frame)),
        "causal_events": int(frame["event_group_id"].nunique()),
        "feature_count": len(FEATURE_NAMES),
        "split": split_report,
        "platt": {"coefficient": coefficient, "intercept": intercept},
        "splits": report_splits,
        "feature_importance": importance,
        "runtime_parity_rows": parity_rows,
    }
    Path(args.report_output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata, report


def main() -> None:
    metadata, report = train(parse_args())
    print(
        json.dumps(
            {
                "model_id": metadata["model_id"],
                "rows": report["rows"],
                "causal_events": report["causal_events"],
                "test_calibrated_prediction": report["splits"]["test"][
                    "calibrated_prediction"
                ],
                "test_same_bucket_arbitration": report["splits"]["test"][
                    "same_bucket_arbitration"
                ],
                "top_features": report["feature_importance"][:25],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
