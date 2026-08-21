#!/usr/bin/env python3
"""Train one cross-environment, cross-symbol robust EasyChart plan router."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from robust_router_system import row_feature_record, train_robust_router


def _parse_input(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--input must be ENVIRONMENT=/path/to/counterfactual_plans.csv")
    environment, path = value.split("=", 1)
    if not environment.strip():
        raise argparse.ArgumentTypeError("empty environment label")
    return environment.strip(), Path(path)


def _timestamp_ns(frame: pd.DataFrame) -> np.ndarray:
    for name in ("observed_time_ns", "ts_ns"):
        if name in frame.columns:
            values = pd.to_numeric(frame[name], errors="coerce")
            if values.notna().all():
                return values.astype("int64").to_numpy()
    raise ValueError("counterfactual table needs complete observed_time_ns or ts_ns")


def _resolved(frame: pd.DataFrame) -> pd.DataFrame:
    if "counterfactual_outcome" not in frame.columns:
        raise ValueError("counterfactual_outcome is missing")
    mask = frame["counterfactual_outcome"].isin(
        ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"],
    )
    if "economic_geometry_viable" in frame.columns:
        viable = frame["economic_geometry_viable"].astype(str).str.lower().isin(
            ["true", "1", "1.0"],
        )
        mask &= viable
    return frame.loc[mask].copy()


def _episode_time(frame: pd.DataFrame) -> pd.Series:
    for name in ("interaction_time_ns", "ts_ns", "observed_time_ns"):
        if name in frame.columns:
            value = pd.to_numeric(frame[name], errors="coerce")
            if value.notna().any():
                return value.fillna(-1).astype("int64")
    return pd.Series(np.arange(len(frame)), index=frame.index, dtype="int64")


def _balanced_weights(frame: pd.DataFrame) -> np.ndarray:
    work = frame[["environment", "symbol", "side"]].copy()
    work["episode_time"] = _episode_time(frame)
    episode_size = work.groupby(
        ["environment", "symbol", "side", "episode_time"],
        dropna=False,
    )["episode_time"].transform("size").astype(float)
    raw = 1.0 / episode_size
    totals = raw.groupby([work["environment"], work["symbol"]]).transform("sum")
    balanced = raw / totals.replace(0.0, np.nan)
    balanced = balanced.fillna(0.0).to_numpy(dtype=np.float64)
    balanced *= len(balanced) / max(balanced.sum(), 1e-12)
    return balanced


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=_parse_input, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--min-category-count", type=int, default=8)
    parser.add_argument("--trees", type=int, default=44)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--feature-subsample", type=int, default=56)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()

    frames: list[pd.DataFrame] = []
    input_records: dict[str, Any] = {}
    for environment, path in args.input:
        if not path.exists():
            raise FileNotFoundError(path)
        frame = _resolved(pd.read_csv(path, low_memory=False))
        if frame.empty:
            raise RuntimeError(f"no resolved plans in {environment}: {path}")
        frame["environment"] = environment
        frames.append(frame)
        input_records[environment] = {
            "path": str(path),
            "resolved_rows": int(len(frame)),
            "outcomes": {
                str(key): int(value)
                for key, value in frame["counterfactual_outcome"].value_counts().items()
            },
            "symbols": {
                str(key): int(value)
                for key, value in frame["symbol"].value_counts().items()
            },
        }
    development = pd.concat(frames, ignore_index=True, sort=False)
    timestamps = _timestamp_ns(development)
    labels = (
        development["counterfactual_outcome"] == "TARGET_FIRST"
    ).astype(int).to_numpy()
    records = [
        row_feature_record(row)
        for row in development.to_dict(orient="records")
    ]
    weights = _balanced_weights(development)
    model = train_robust_router(
        records,
        labels,
        timestamps,
        development["environment"].astype(str).to_numpy(),
        development["symbol"].astype(str).to_numpy(),
        weights,
        min_category_count=args.min_category_count,
        trees=args.trees,
        depth=args.depth,
        feature_subsample=args.feature_subsample,
        seed=args.seed,
        metadata={
            "inputs": input_records,
            "label_policy": (
                "TARGET_FIRST_IS_ONE; STOP_FIRST_AND_SAME_MINUTE_AMBIGUITY_ARE_ZERO; "
                "UNRESOLVED_IS_NOT_A_TRAINING_LABEL"
            ),
            "weighting": (
                "EQUAL_TOTAL_WEIGHT_PER_ENVIRONMENT_SYMBOL_AFTER_ONE_UNIT_IS_SHARED_"
                "BY_ALTERNATIVE_PLANS_FROM_THE_SAME_SIDE_AND_INTERACTION_TIME"
            ),
            "feature_information_time": "PLAN_EMISSION_OR_EARLIER_ONLY",
        },
    )
    model.save(args.output)
    summary = model.training_metadata
    destination = args.summary_output or args.output.with_suffix(".summary.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
