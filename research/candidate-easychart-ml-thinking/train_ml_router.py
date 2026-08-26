#!/usr/bin/env python3
"""Train the causal EasyChart plan router from fixed counterfactual plans."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml_router import row_feature_record, train_router


def _timestamp_ns(frame: pd.DataFrame) -> np.ndarray:
    for name in ("observed_time_ns", "ts_ns"):
        if name in frame.columns:
            values = pd.to_numeric(frame[name], errors="coerce")
            if values.notna().all():
                return values.astype("int64").to_numpy()
    raise ValueError("input must contain complete observed_time_ns or ts_ns")


def _episode_weights(frame: pd.DataFrame) -> np.ndarray:
    keys = [name for name in ("ts_ns", "causal_event_id") if name in frame.columns]
    if not keys:
        return np.ones(len(frame), dtype=np.float64)
    group_size = frame.groupby(keys, dropna=False)[keys[0]].transform("size")
    return 1.0 / group_size.astype(float).to_numpy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-start-ns", type=int)
    parser.add_argument("--train-end-ns", type=int)
    parser.add_argument("--min-category-count", type=int, default=5)
    parser.add_argument("--cv-folds", type=int, default=3)
    args = parser.parse_args()

    frame = pd.read_csv(args.input, low_memory=False)
    if "counterfactual_outcome" not in frame.columns:
        raise ValueError("fixed counterfactual CSV is missing counterfactual_outcome")
    timestamps = _timestamp_ns(frame)
    mask = frame["counterfactual_outcome"].isin(
        ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"],
    ).to_numpy()
    if "economic_geometry_viable" in frame.columns:
        viable = frame["economic_geometry_viable"].astype(str).str.lower().isin(
            ["true", "1", "1.0"],
        )
        mask &= viable.to_numpy()
    if args.train_start_ns is not None:
        mask &= timestamps >= args.train_start_ns
    if args.train_end_ns is not None:
        mask &= timestamps <= args.train_end_ns
    development = frame.loc[mask].copy().reset_index(drop=True)
    timestamps = _timestamp_ns(development)
    labels = (development["counterfactual_outcome"] == "TARGET_FIRST").astype(int).to_numpy()
    records: list[dict[str, Any]] = [
        row_feature_record(row)
        for row in development.to_dict(orient="records")
    ]
    model = train_router(
        records,
        labels,
        timestamps,
        sample_weights=_episode_weights(development),
        min_category_count=args.min_category_count,
        cv_folds=args.cv_folds,
        metadata={
            "input": str(args.input),
            "resolved_outcomes": {
                key: int(value)
                for key, value in development["counterfactual_outcome"].value_counts().items()
            },
            "ambiguous_same_minute_is_stop": True,
            "episode_weighting": "inverse candidate count per (ts_ns, causal_event_id)",
            "feature_information_time": "plan emission time or earlier only",
        },
    )
    model.save(args.output)
    print(json.dumps(model.training_metadata, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
