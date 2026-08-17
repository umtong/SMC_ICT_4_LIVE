#!/usr/bin/env python3
"""Train ML3 with purged chronological expanding out-of-fold predictions.

A plan's first-passage label may resolve after the next model-validation boundary.
Such a row cannot enter the earlier training fold, even though its plan timestamp
precedes that boundary.  This module reuses the causal feature/model pipeline and
purges those crossing label intervals before every expanding fold.  The final
model still uses the complete, self-contained development period; the purge is
for honest OOF evidence and any future walk-forward fit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

import train_ml3_meta_model as _base
from ml3_meta_model import ML3MetaModel


PURGED_TRAINING_POLICY = (
    _base.TRAINING_POLICY
    + ";PURGE_TRAIN_LABEL_INTERVALS_REACHING_EACH_VALIDATION_START"
)


def parse_args() -> argparse.Namespace:
    parser = _base.parse_args()
    # ``parse_args`` above has already consumed sys.argv, so this function is
    # intentionally not used by ``main``.  It exists only as a discoverable API.
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--oof-output", type=Path, required=True)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--minimum-oof-train", type=int, default=100)
    parser.add_argument("--embargo-minutes", type=int, default=0)
    parser.add_argument("--l2", type=float, default=0.03)
    parser.add_argument("--feature-clip", type=float, default=8.0)
    return parser


def _label_end_ns(frame: pd.DataFrame) -> np.ndarray:
    if "counterfactual_resolution_time" not in frame.columns:
        raise RuntimeError(
            "ML3 purged training requires counterfactual_resolution_time"
        )
    timestamps = pd.to_datetime(
        frame["counterfactual_resolution_time"],
        utc=True,
        errors="coerce",
    )
    if timestamps.isna().any():
        sample = frame.loc[timestamps.isna(), ["plan_id", "counterfactual_resolution_time"]]
        raise RuntimeError(
            "resolved ML3 row has an invalid label end time:\n"
            + sample.head(20).to_string(index=False)
        )
    return timestamps.astype("int64").to_numpy(dtype=np.int64)


def purged_expanding_folds(
    timestamps: Sequence[int],
    label_end_ns: Sequence[int],
    *,
    folds: int,
    minimum_train_rows: int,
    embargo_minutes: int = 0,
) -> list[tuple[np.ndarray, np.ndarray, dict[str, int]]]:
    if folds < 1:
        raise ValueError("folds must be positive")
    if minimum_train_rows < 1:
        raise ValueError("minimum_train_rows must be positive")
    if embargo_minutes < 0:
        raise ValueError("embargo_minutes cannot be negative")
    ts = np.asarray(timestamps, dtype=np.int64)
    ends = np.asarray(label_end_ns, dtype=np.int64)
    if ts.shape != ends.shape:
        raise ValueError("timestamps and label_end_ns must align")
    unique = np.unique(ts)
    if unique.size < 3:
        return []
    embargo_ns = int(embargo_minutes) * 60_000_000_000

    first_index: int | None = None
    for index in range(1, unique.size):
        boundary = int(unique[index])
        eligible = (ts < boundary) & (ends < boundary - embargo_ns)
        if int(eligible.sum()) >= minimum_train_rows:
            first_index = index
            break
    if first_index is None or first_index >= unique.size:
        return []

    remaining = unique.size - first_index
    effective_folds = min(folds, remaining)
    boundaries = np.linspace(first_index, unique.size, effective_folds + 1, dtype=int)
    output: list[tuple[np.ndarray, np.ndarray, dict[str, int]]] = []
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end <= start:
            continue
        validation_times = unique[start:end]
        validation_start = int(validation_times[0])
        raw_train_mask = ts < validation_start
        train_mask = raw_train_mask & (ends < validation_start - embargo_ns)
        validation_mask = np.isin(ts, validation_times)
        train_index = np.flatnonzero(train_mask)
        validation_index = np.flatnonzero(validation_mask)
        if len(train_index) < minimum_train_rows or len(validation_index) == 0:
            continue
        output.append(
            (
                train_index,
                validation_index,
                {
                    "validation_start_ns": validation_start,
                    "validation_end_ns": int(validation_times[-1]),
                    "raw_train_rows": int(raw_train_mask.sum()),
                    "train_rows": int(len(train_index)),
                    "purged_train_rows": int(raw_train_mask.sum() - len(train_index)),
                    "validation_rows": int(len(validation_index)),
                    "embargo_minutes": int(embargo_minutes),
                },
            )
        )
    return output


def train(
    input_path: Path,
    *,
    folds: int,
    minimum_oof_train: int,
    embargo_minutes: int,
    l2: float,
    feature_clip: float,
) -> tuple[ML3MetaModel, dict[str, Any], pd.DataFrame]:
    frame, features, labels, dropped = _base._load_rows(input_path)
    ends = _label_end_ns(frame)
    fold_indices = purged_expanding_folds(
        frame["ts_ns"].tolist(),
        ends,
        folds=folds,
        minimum_train_rows=minimum_oof_train,
        embargo_minutes=embargo_minutes,
    )
    oof_records: list[dict[str, Any]] = []
    fold_reports: list[dict[str, int]] = []
    for fold_number, (train_index, validation_index, fold_report) in enumerate(
        fold_indices,
        start=1,
    ):
        model = ML3MetaModel.fit(
            [features[index] for index in train_index],
            labels[train_index],
            l2=l2,
            feature_clip=feature_clip,
            training={"fold": fold_number, **fold_report},
        )
        fold_reports.append({"fold": fold_number, **fold_report})
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
                    "label_end_ns": int(ends[index]),
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
                    "fold_train_rows": fold_report["train_rows"],
                    "fold_purged_train_rows": fold_report["purged_train_rows"],
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
            "policy": PURGED_TRAINING_POLICY,
            "input_sha256": input_sha256,
            "input_path": str(input_path),
            "start_ts_ns": int(frame["ts_ns"].min()),
            "end_ts_ns": int(frame["ts_ns"].max()),
            "maximum_label_end_ns": int(ends.max()),
            "resolved_usable_plans": int(len(frame)),
            "target_first": int(labels.sum()),
            "non_target_first": int(len(labels) - labels.sum()),
            "dropped_feature_rows": int(sum(dropped.values())),
            "dropped_reasons": dict(dropped.most_common(20)),
            "oof_folds": int(len(fold_indices)),
            "oof_embargo_minutes": int(embargo_minutes),
        },
    )

    report: dict[str, Any] = {
        "training_policy": PURGED_TRAINING_POLICY,
        "input": str(input_path),
        "input_sha256": input_sha256,
        "resolved_usable_plans": int(len(frame)),
        "target_first": int(labels.sum()),
        "non_target_first": int(len(labels) - labels.sum()),
        "dropped_feature_rows": int(sum(dropped.values())),
        "dropped_reasons": dict(dropped.most_common(20)),
        "l2": float(l2),
        "feature_clip": float(feature_clip),
        "embargo_minutes": int(embargo_minutes),
        "model_feature_count": final_model.feature_count,
        "model_training": final_model.training,
        "oof_folds": fold_reports,
        "oof": _base._summary_frame(oof),
        "oof_calibration": _base._calibration(oof),
        "oof_by_family": {
            str(family): _base._summary_frame(group)
            for family, group in oof.groupby("family", dropna=False)
        }
        if not oof.empty
        else {},
        "oof_by_symbol": {
            str(symbol): _base._summary_frame(group)
            for symbol, group in oof.groupby("symbol", dropna=False)
        }
        if not oof.empty
        else {},
    }
    return final_model, report, oof


def main() -> None:
    args = build_parser().parse_args()
    model, report, oof = train(
        args.input,
        folds=args.folds,
        minimum_oof_train=args.minimum_oof_train,
        embargo_minutes=args.embargo_minutes,
        l2=args.l2,
        feature_clip=args.feature_clip,
    )
    model.save(args.model_output)
    report["model_output"] = str(args.model_output)
    report["model_sha256"] = model.sha256
    _base._write_json(args.report_output, report)
    args.oof_output.parent.mkdir(parents=True, exist_ok=True)
    oof.to_csv(args.oof_output, index=False)
    print(
        json.dumps(
            _base._safe_json(report),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
