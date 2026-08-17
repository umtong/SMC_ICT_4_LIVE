#!/usr/bin/env python3
"""Train the causal EasyChart RE1 ML3 target-before-stop meta-model.

Input is the research-only counterfactual plan table. Future bars supply the
binary label only; deployment features are rebuilt through ``offline_feature_row``
so the fitted model receives exactly the fields that the live/backtest router can
construct at the completed-minute watermark.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ml3_meta_model import ML3MetaModel
from ml3_online_features import FeatureUnavailable, offline_feature_row


RESOLVED_OUTCOMES = {
    "TARGET_FIRST": 1,
    "STOP_FIRST": 0,
    "AMBIGUOUS_SAME_MINUTE": 0,
}
TRAINING_POLICY = (
    "RESEARCH_ONLY_FUTURE_FIRST_PASSAGE_LABEL;DEPLOYMENT_FEATURES_REBUILT_WITH_"
    "THE_ONLINE_SCHEMA;CHRONOLOGICAL_EXPANDING_OOF;SYMBOL_IS_DIAGNOSTIC_NOT_A_FEATURE"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--oof-output", type=Path, required=True)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--minimum-oof-train", type=int, default=100)
    parser.add_argument("--l2", type=float, default=0.03)
    parser.add_argument("--feature-clip", type=float, default=8.0)
    return parser.parse_args()


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if not math.isfinite(number) else number
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_safe_json(dict(payload)), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _log_loss(labels: np.ndarray, probabilities: np.ndarray) -> float:
    p = np.clip(probabilities.astype(float), 1e-9, 1.0 - 1e-9)
    y = labels.astype(float)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def _auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    y = labels.astype(int)
    positive = int(y.sum())
    negative = int(len(y) - positive)
    if positive == 0 or negative == 0:
        return None
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    ranks = np.empty(len(scores), dtype=float)
    start = 0
    while start < len(scores):
        end = start + 1
        while end < len(scores) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        average_rank = 0.5 * ((start + 1) + end)
        ranks[order[start:end]] = average_rank
        start = end
    rank_sum = float(ranks[y == 1].sum())
    return (rank_sum - positive * (positive + 1) / 2.0) / (positive * negative)


def _account_payoffs(target_net_r: float, stop_net_r: float) -> tuple[float, float]:
    target = float(target_net_r)
    stop = float(stop_net_r)
    if not math.isfinite(target) or not math.isfinite(stop):
        raise ValueError("non-finite counterfactual economics")
    if target <= 0.0 or stop >= 0.0:
        raise ValueError("counterfactual economics require positive target and negative stop")
    return target / abs(stop), -1.0


def _summary_frame(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"plans": 0}
    labels = frame["label"].to_numpy(dtype=float)
    probabilities = frame["target_first_probability"].to_numpy(dtype=float)
    selected = frame["expected_account_r"] > 0.0
    result: dict[str, Any] = {
        "plans": int(len(frame)),
        "target_first_rate": float(labels.mean()),
        "log_loss": _log_loss(labels, probabilities),
        "brier": float(np.mean((probabilities - labels) ** 2)),
        "auc": _auc(labels, probabilities),
        "positive_expected_plans": int(selected.sum()),
        "positive_expected_fraction": float(selected.mean()),
    }
    if selected.any():
        chosen = frame[selected]
        result.update(
            {
                "positive_expected_mean_prediction_r": float(
                    chosen["expected_account_r"].mean()
                ),
                "positive_expected_realized_mean_r": float(
                    chosen["realized_account_r"].mean()
                ),
                "positive_expected_realized_sum_r": float(
                    chosen["realized_account_r"].sum()
                ),
                "positive_expected_target_first_rate": float(chosen["label"].mean()),
            }
        )
    return result


def _calibration(frame: pd.DataFrame, bins: int = 10) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    ordered = frame.sort_values("target_first_probability", kind="mergesort")
    groups = np.array_split(np.arange(len(ordered)), min(bins, len(ordered)))
    output: list[dict[str, Any]] = []
    for index, positions in enumerate(groups, start=1):
        if len(positions) == 0:
            continue
        group = ordered.iloc[positions]
        output.append(
            {
                "bin": index,
                "plans": int(len(group)),
                "mean_probability": float(group["target_first_probability"].mean()),
                "target_first_rate": float(group["label"].mean()),
                "mean_expected_account_r": float(group["expected_account_r"].mean()),
                "mean_realized_account_r": float(group["realized_account_r"].mean()),
            }
        )
    return output


def _expanding_folds(
    timestamps: Sequence[int],
    *,
    folds: int,
    minimum_train_rows: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    if folds < 1:
        raise ValueError("folds must be positive")
    ts = np.asarray(timestamps, dtype=np.int64)
    unique = np.unique(ts)
    if unique.size < 3:
        return []
    counts = np.asarray([(ts == value).sum() for value in unique], dtype=int)
    cumulative = np.cumsum(counts)
    first_index = int(np.searchsorted(cumulative, minimum_train_rows, side="left") + 1)
    first_index = max(1, min(first_index, unique.size - 1))
    remaining = unique.size - first_index
    if remaining <= 0:
        return []
    effective_folds = min(folds, remaining)
    boundaries = np.linspace(first_index, unique.size, effective_folds + 1, dtype=int)
    output: list[tuple[np.ndarray, np.ndarray]] = []
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end <= start:
            continue
        validation_times = unique[start:end]
        train_mask = ts < validation_times[0]
        validation_mask = np.isin(ts, validation_times)
        train_index = np.flatnonzero(train_mask)
        validation_index = np.flatnonzero(validation_mask)
        if len(train_index) < minimum_train_rows or len(validation_index) == 0:
            continue
        output.append((train_index, validation_index))
    return output


def _load_rows(path: Path) -> tuple[pd.DataFrame, list[dict[str, Any]], np.ndarray, Counter[str]]:
    raw = pd.read_csv(path, low_memory=False)
    required = {
        "counterfactual_outcome",
        "counterfactual_target_net_r",
        "counterfactual_stop_net_r",
        "ts_ns",
        "symbol",
        "family",
        "plan_id",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise RuntimeError(f"ML3 training input is missing columns: {missing}")
    raw = raw[raw["counterfactual_outcome"].isin(RESOLVED_OUTCOMES)].copy()
    raw["ts_ns"] = pd.to_numeric(raw["ts_ns"], errors="raise").astype("int64")
    raw = raw.sort_values(["ts_ns", "symbol", "plan_id"], kind="mergesort").reset_index(drop=True)

    kept_rows: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    labels: list[int] = []
    dropped: Counter[str] = Counter()
    for record in raw.to_dict(orient="records"):
        try:
            feature = offline_feature_row(record)
            target_r, stop_r = _account_payoffs(
                float(record["counterfactual_target_net_r"]),
                float(record["counterfactual_stop_net_r"]),
            )
        except (FeatureUnavailable, ValueError, TypeError, KeyError) as exc:
            dropped[type(exc).__name__ + ":" + str(exc)] += 1
            continue
        record["target_account_r"] = target_r
        record["stop_account_r"] = stop_r
        kept_rows.append(record)
        features.append(feature)
        labels.append(int(RESOLVED_OUTCOMES[str(record["counterfactual_outcome"])]))
    if not kept_rows:
        raise RuntimeError("ML3 training has no usable resolved plans")
    frame = pd.DataFrame(kept_rows).reset_index(drop=True)
    y = np.asarray(labels, dtype=int)
    if np.unique(y).size != 2:
        raise RuntimeError("ML3 training needs both target-first and stop-first plans")
    return frame, features, y, dropped


def train(
    input_path: Path,
    *,
    folds: int,
    minimum_oof_train: int,
    l2: float,
    feature_clip: float,
) -> tuple[ML3MetaModel, dict[str, Any], pd.DataFrame]:
    frame, features, labels, dropped = _load_rows(input_path)
    fold_indices = _expanding_folds(
        frame["ts_ns"].tolist(),
        folds=folds,
        minimum_train_rows=minimum_oof_train,
    )
    oof_records: list[dict[str, Any]] = []
    for fold_number, (train_index, validation_index) in enumerate(fold_indices, start=1):
        model = ML3MetaModel.fit(
            [features[index] for index in train_index],
            labels[train_index],
            l2=l2,
            feature_clip=feature_clip,
            training={"fold": fold_number},
        )
        for index in validation_index:
            probability = model.predict_probability(features[index])
            target_r = float(frame.iloc[index]["target_account_r"])
            stop_r = -1.0
            expected_r = probability * target_r + (1.0 - probability) * stop_r
            label = int(labels[index])
            base = frame.iloc[index]
            oof_records.append(
                {
                    "fold": fold_number,
                    "source_row": int(index),
                    "ts_ns": int(base["ts_ns"]),
                    "symbol": str(base["symbol"]),
                    "family": str(base["family"]),
                    "plan_id": str(base["plan_id"]),
                    "label": label,
                    "target_first_probability": probability,
                    "target_account_r": target_r,
                    "stop_account_r": stop_r,
                    "break_even_probability": 1.0 / (1.0 + target_r),
                    "expected_account_r": expected_r,
                    "realized_account_r": target_r if label == 1 else stop_r,
                }
            )
    oof = pd.DataFrame(oof_records)

    input_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
    final_model = ML3MetaModel.fit(
        features,
        labels,
        l2=l2,
        feature_clip=feature_clip,
        training={
            "policy": TRAINING_POLICY,
            "input_sha256": input_sha256,
            "input_path": str(input_path),
            "start_ts_ns": int(frame["ts_ns"].min()),
            "end_ts_ns": int(frame["ts_ns"].max()),
            "resolved_usable_plans": int(len(frame)),
            "target_first": int(labels.sum()),
            "non_target_first": int(len(labels) - labels.sum()),
            "dropped_feature_rows": int(sum(dropped.values())),
            "dropped_reasons": dict(dropped.most_common(20)),
            "oof_folds": int(len(fold_indices)),
        },
    )

    report: dict[str, Any] = {
        "training_policy": TRAINING_POLICY,
        "input": str(input_path),
        "input_sha256": input_sha256,
        "resolved_usable_plans": int(len(frame)),
        "target_first": int(labels.sum()),
        "non_target_first": int(len(labels) - labels.sum()),
        "dropped_feature_rows": int(sum(dropped.values())),
        "dropped_reasons": dict(dropped.most_common(20)),
        "l2": float(l2),
        "feature_clip": float(feature_clip),
        "model_feature_count": final_model.feature_count,
        "model_training": final_model.training,
        "oof": _summary_frame(oof),
        "oof_calibration": _calibration(oof),
        "oof_by_family": {
            str(family): _summary_frame(group)
            for family, group in oof.groupby("family", dropna=False)
        }
        if not oof.empty
        else {},
        "oof_by_symbol": {
            str(symbol): _summary_frame(group)
            for symbol, group in oof.groupby("symbol", dropna=False)
        }
        if not oof.empty
        else {},
    }
    return final_model, report, oof


def main() -> None:
    args = parse_args()
    model, report, oof = train(
        args.input,
        folds=args.folds,
        minimum_oof_train=args.minimum_oof_train,
        l2=args.l2,
        feature_clip=args.feature_clip,
    )
    model.save(args.model_output)
    report["model_output"] = str(args.model_output)
    report["model_sha256"] = model.sha256
    _write_json(args.report_output, report)
    args.oof_output.parent.mkdir(parents=True, exist_ok=True)
    oof.to_csv(args.oof_output, index=False)
    print(json.dumps(_safe_json(report), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
