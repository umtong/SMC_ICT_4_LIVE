#!/usr/bin/env python3
"""Candidate 4t v6: causal trajectory representation over v5 decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import candidate_4t_policy_v5 as v5

core = v5.core
base = v5.base
ORIGINAL_LOAD_ACTIONS = base.load_actions
TRAJECTORY_SOURCES: set[str] = set()

PRIORITY_SOURCES = (
    "auction_progress_r",
    "auction_retrace_fraction",
    "auction_outside_close_ratio",
    "auction_outside_volume_ratio",
    "auction_path_efficiency",
    "auction_effort_result",
    "auction_failure_pressure",
    "auction_response_efficiency",
    "auction_control_transfer",
    "auction_absorption_ratio",
    "auction_delta_share",
    "auction_activity_ratio",
    "approach_path_efficiency",
    "approach_distance_compression",
    "approach_activity_ratio",
    "approach_delta_share",
    "approach_impact_per_activity",
)
SOURCE_PREFIXES = ("auction_", "approach_", "response_", "control_")
SOURCE_EXCLUDES = (
    "label", "future", "actual", "outcome", "filled", "resolved", "win",
    "net_r", "mfe", "mae", "holding", "entry_wait", "target", "stop",
    "entry", "route", "risk", "geometry", "time_ns", "index",
)


def choose_trajectory_sources(frame: pd.DataFrame, limit: int = 14) -> list[str]:
    sources: list[str] = []
    for column in PRIORITY_SOURCES:
        if column in frame and pd.api.types.is_numeric_dtype(frame[column]):
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.notna().sum() >= 20 and values.nunique(dropna=True) > 1:
                sources.append(column)
    for column in frame.columns:
        if column in sources or not column.startswith(SOURCE_PREFIXES):
            continue
        low = column.lower()
        if any(token in low for token in SOURCE_EXCLUDES):
            continue
        if not pd.api.types.is_numeric_dtype(frame[column]):
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().sum() < 20 or values.nunique(dropna=True) <= 1:
            continue
        sources.append(column)
        if len(sources) >= limit:
            break
    return sources[:limit]


def add_causal_trajectory_features(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    hypothesis = ["period", "episode_id", "family", "side"]
    states = (
        frame.sort_values(keys + ["action_id"])
        .drop_duplicates(keys, keep="first")
        .copy()
    )
    sources = choose_trajectory_sources(states)
    TRAJECTORY_SOURCES.update(sources)
    feature_names: list[str] = []
    trajectory_parts: list[pd.DataFrame] = []
    for _, group in states.groupby(hypothesis, sort=False, dropna=False):
        group = group.sort_values(["order_time_ns", "state_id"]).copy()
        start = pd.to_numeric(group.order_time_ns, errors="coerce").iloc[0]
        group["trajectory_update_count"] = np.arange(1, len(group) + 1, dtype=float)
        group["trajectory_age_minutes"] = (
            pd.to_numeric(group.order_time_ns, errors="coerce") - float(start)
        ) / base.NS_PER_MINUTE
        phase = group.get(
            "auction_phase", pd.Series("UNKNOWN", index=group.index)
        ).fillna("UNKNOWN").astype(str)
        group["trajectory_phase_changed"] = phase.ne(phase.shift(1)).astype(float)
        if len(group):
            group.iloc[0, group.columns.get_loc("trajectory_phase_changed")] = 0.0
        group["trajectory_phase_change_count"] = group[
            "trajectory_phase_changed"
        ].cumsum()
        for column in sources:
            values = pd.to_numeric(group[column], errors="coerce").astype(float)
            prior = values.shift(1)
            delta = values - prior
            prior_ema = values.shift(1).ewm(alpha=0.45, adjust=False).mean()
            prior_min = values.shift(1).cummin()
            prior_max = values.shift(1).cummax()
            consistency = np.sign(delta).rolling(3, min_periods=1).mean()
            generated = {
                f"trajectory_delta__{column}": delta,
                f"trajectory_prior_ema_gap__{column}": values - prior_ema,
                f"trajectory_above_prior_min__{column}": values - prior_min,
                f"trajectory_below_prior_max__{column}": prior_max - values,
                f"trajectory_change_consistency__{column}": consistency,
            }
            for name, series in generated.items():
                group[name] = series.replace([np.inf, -np.inf], np.nan).fillna(0.0)
                feature_names.append(name)
        trajectory_parts.append(group)
    state_features = pd.concat(trajectory_parts, ignore_index=True, sort=False)
    feature_names = sorted(set(
        [
            "trajectory_update_count", "trajectory_age_minutes",
            "trajectory_phase_changed", "trajectory_phase_change_count",
        ] + feature_names
    ))
    state_features = state_features[["period", "state_id"] + feature_names]
    output = frame.merge(
        state_features,
        on=["period", "state_id"],
        how="left",
        validate="many_to_one",
    )
    for column in feature_names:
        output[column] = pd.to_numeric(output[column], errors="coerce").fillna(0.0)
    output.attrs["trajectory_sources"] = sources
    output.attrs["trajectory_features"] = feature_names
    return output


def load_augmented_actions(root: Path) -> pd.DataFrame:
    return add_causal_trajectory_features(ORIGINAL_LOAD_ACTIONS(root))


base.load_actions = load_augmented_actions


def run(
    development_root: Path,
    fresh_root: Path | None,
    output: Path,
) -> dict[str, Any]:
    result = v5.run(development_root, fresh_root, output)
    result["policy"] = "CANDIDATE_4T_V6_CAUSAL_AUCTION_TRAJECTORIES"
    result["trajectory_sources"] = sorted(TRAJECTORY_SOURCES)
    result["trajectory_contract"] = (
        "current and prior states only; keyed by episode/family/side; actions deduplicated"
    )
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    manifest_path = output / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["trajectory_sources"] = sorted(TRAJECTORY_SOURCES)
    manifest["trajectory_contract"] = result["trajectory_contract"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output / "RESULT.md").write_text(
        "# Candidate 4t v6 causal trajectory result\n\n"
        "One-account evidence for the precommitted trajectory architecture. "
        "All trajectory features are causal expanding-state features; development is "
        "leave-one-period-out and fresh data is not fitted.\n\n```json\n"
        + json.dumps(result, ensure_ascii=False, indent=2, default=str)
        + "\n```\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.development_root, args.fresh_root, args.output)


if __name__ == "__main__":
    main()
