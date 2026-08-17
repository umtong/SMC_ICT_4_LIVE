#!/usr/bin/env python3
"""Join ML3v3 causal features, complete plan geometry and post-run labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from features_ml3v3 import FEATURE_DEFAULTS, FEATURE_NAMES


LABEL_POLICY = (
    "TARGET_FIRST=1;STOP_FIRST=0;AMBIGUOUS_SAME_MINUTE=0;"
    "UNRESOLVED_EXCLUDED;FEATURES_AND_PLAN_GEOMETRY_PRECEDE_LABEL_RESOLUTION"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-output", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--counterfactual", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    return parser.parse_args()


def _unique(frame: pd.DataFrame, kind: str) -> pd.DataFrame:
    selected = frame[frame["kind"] == kind].copy()
    if selected.empty:
        raise RuntimeError(f"decision events contain no kind={kind!r} rows")
    if selected["plan_id"].isna().any():
        raise RuntimeError(f"kind={kind!r} row is missing plan_id")
    selected = selected.sort_values(["plan_id", "ts_ns"], kind="mergesort")
    duplicate = selected[selected["plan_id"].duplicated(keep=False)]
    if not duplicate.empty:
        sample_columns = [
            name for name in ("plan_id", "ts_ns", "symbol", "family")
            if name in duplicate.columns
        ]
        raise RuntimeError(
            f"duplicate {kind} identities:\n"
            + duplicate[sample_columns].head(30).to_string(index=False)
        )
    return selected


def build_dataset(
    *,
    events_path: Path,
    counterfactual_path: Path,
    output_path: Path,
    summary_path: Path,
) -> dict[str, Any]:
    events = pd.read_csv(events_path, low_memory=False)
    features = _unique(events, "ml_plan")
    plans = _unique(events, "plan")

    feature_columns = [f"mlf_{name}" for name in FEATURE_NAMES]
    missing = [column for column in feature_columns if column not in features.columns]
    if missing:
        raise RuntimeError(
            "decision_events.csv does not match the ML3v3 feature schema; "
            f"missing={missing[:12]}"
        )

    feature_keep = [
        "plan_id",
        "ts_ns",
        "model_id",
        "model_status",
        "ml_mode",
        "ml_win_net_r",
        "ml_loss_net_r",
        "ml_break_even_probability",
        *feature_columns,
    ]
    feature_keep = [column for column in feature_keep if column in features.columns]
    plan_keep = [
        "plan_id",
        "causal_event_id",
        "symbol",
        "family",
        "side",
        "scenario_path",
        "scale_name",
        "observed_time_ns",
        "interaction_time_ns",
        "trigger_time_ns",
        "setup_observed_time_ns",
        "entry",
        "stop",
        "target",
        "gross_rr",
        "overlap_lower",
        "overlap_upper",
        "higher_timeframe_minutes",
        "decision_timeframe_minutes",
        "trigger_timeframe_minutes",
        "higher_zone_kind",
        "lower_zone_kind",
        "trigger_zone_kind",
        "target_zone_kind",
    ]
    plan_keep = [column for column in plan_keep if column in plans.columns]
    merged = features[feature_keep].merge(
        plans[plan_keep],
        on="plan_id",
        how="inner",
        validate="one_to_one",
    )
    if merged.empty:
        raise RuntimeError("no ML feature row matched a complete plan row")

    labels = pd.read_csv(counterfactual_path, low_memory=False)
    if labels.empty or labels["plan_id"].duplicated().any():
        raise RuntimeError("counterfactual plan labels must be nonempty and unique")
    outcome_keep = [
        "plan_id",
        "counterfactual_outcome",
        "counterfactual_resolution_time",
        "counterfactual_minutes_to_resolution",
        "counterfactual_net_r_conservative",
        "counterfactual_target_net_r",
        "counterfactual_stop_net_r",
        "post_cost_break_even_target_probability",
        "risk_bps",
        "target_bps",
    ]
    outcome_keep = [column for column in outcome_keep if column in labels.columns]
    merged = merged.merge(
        labels[outcome_keep],
        on="plan_id",
        how="inner",
        validate="one_to_one",
    )
    if merged.empty:
        raise RuntimeError("no complete ML3v3 plan matched a first-passage label")

    merged["label"] = merged["counterfactual_outcome"].map(
        {
            "TARGET_FIRST": 1.0,
            "STOP_FIRST": 0.0,
            "AMBIGUOUS_SAME_MINUTE": 0.0,
        }
    )
    merged = merged[merged["label"].notna()].copy()
    if merged.empty:
        raise RuntimeError("ML3v3 dataset contains no resolved binary outcomes")
    merged["label"] = merged["label"].astype(int)

    event_time = pd.to_numeric(
        merged.get("observed_time_ns", merged["ts_ns"]),
        errors="raise",
    ).astype("int64")
    merged["event_time_ns"] = event_time
    resolution = pd.to_datetime(
        merged["counterfactual_resolution_time"],
        utc=True,
        errors="coerce",
    )
    if resolution.isna().any():
        raise RuntimeError("resolved plan has an invalid counterfactual resolution time")
    merged["label_end_ns"] = resolution.astype("int64")
    merged["event_date"] = pd.to_datetime(
        merged["event_time_ns"], unit="ns", utc=True
    ).dt.strftime("%Y-%m-%d")

    if "causal_event_id" not in merged.columns:
        merged["causal_event_id"] = (
            merged["symbol"].astype(str)
            + "|"
            + merged["side"].astype(str)
            + "|"
            + merged["interaction_time_ns"].astype(str)
        )
    merged["event_group_id"] = merged["causal_event_id"].astype(str)
    merged["candidate_count_in_event"] = merged.groupby(
        "event_group_id", sort=False
    )["plan_id"].transform("count")

    for name, column in zip(FEATURE_NAMES, feature_columns, strict=True):
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(
            FEATURE_DEFAULTS[name]
        )
    merged = merged.sort_values(
        ["event_time_ns", "symbol", "event_group_id", "plan_id"],
        kind="mergesort",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False)

    summary: dict[str, Any] = {
        "rows": int(len(merged)),
        "event_groups": int(merged["event_group_id"].nunique()),
        "target_first": int(merged["label"].sum()),
        "stop_or_ambiguous": int(len(merged) - merged["label"].sum()),
        "target_first_rate": float(merged["label"].mean()),
        "feature_count": len(FEATURE_NAMES),
        "label_policy": LABEL_POLICY,
        "events_path": str(events_path),
        "events_sha256": _sha256(events_path),
        "counterfactual_path": str(counterfactual_path),
        "counterfactual_sha256": _sha256(counterfactual_path),
        "output_path": str(output_path),
        "by_symbol": {
            str(key): {
                "rows": int(len(group)),
                "event_groups": int(group["event_group_id"].nunique()),
                "target_first_rate": float(group["label"].mean()),
            }
            for key, group in merged.groupby("symbol", sort=True)
        },
        "by_family": {
            str(key): {
                "rows": int(len(group)),
                "event_groups": int(group["event_group_id"].nunique()),
                "target_first_rate": float(group["label"].mean()),
                "median_minutes_to_resolution": float(
                    pd.to_numeric(
                        group["counterfactual_minutes_to_resolution"],
                        errors="coerce",
                    ).median()
                ),
            }
            for key, group in merged.groupby("family", sort=True, dropna=False)
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    args = parse_args()
    events = args.events or args.run_output / "decision_events.csv"
    counterfactual = args.counterfactual or args.run_output / "counterfactual_plans.csv"
    output = args.output or args.run_output / "ml3v3_dataset.csv"
    summary = args.summary or args.run_output / "ml3v3_dataset_summary.json"
    result = build_dataset(
        events_path=events,
        counterfactual_path=counterfactual,
        output_path=output,
        summary_path=summary,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
