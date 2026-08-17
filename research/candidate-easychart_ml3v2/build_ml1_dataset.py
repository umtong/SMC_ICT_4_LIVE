#!/usr/bin/env python3
"""Join causal ML1 feature events to post-run first-passage labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ml1_features import FEATURE_DEFAULTS, FEATURE_NAMES


LABEL_POLICY = (
    "TARGET_FIRST=1; STOP_FIRST=0; AMBIGUOUS_SAME_MINUTE=0; "
    "UNRESOLVED_EXCLUDED_FROM_BINARY_TRAINING"
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
    parser.add_argument("--keep-unresolved", action="store_true")
    return parser.parse_args()


def build_dataset(
    *,
    events_path: Path,
    counterfactual_path: Path,
    output_path: Path,
    summary_path: Path,
    keep_unresolved: bool = False,
) -> dict[str, Any]:
    events = pd.read_csv(events_path, low_memory=False)
    feature_events = events[events["kind"] == "ml_plan"].copy()
    if feature_events.empty:
        raise RuntimeError(f"no kind=ml_plan rows in {events_path}")
    if feature_events["plan_id"].isna().any():
        raise RuntimeError("ml_plan event missing plan_id")
    feature_events = feature_events.sort_values(["plan_id", "ts_ns"], kind="mergesort")
    duplicate = feature_events[feature_events["plan_id"].duplicated(keep=False)]
    if not duplicate.empty:
        # Repeated IDs indicate a real event-identity error, not extra samples.
        sample = duplicate[["plan_id", "ts_ns", "symbol", "family"]].head(30)
        raise RuntimeError("duplicate ml_plan identities:\n" + sample.to_string(index=False))

    feature_columns = [f"mlf_{name}" for name in FEATURE_NAMES]
    missing = [column for column in feature_columns if column not in feature_events.columns]
    if missing:
        raise RuntimeError(
            "decision_events.csv was not emitted by the current ML1 feature schema; "
            f"missing {missing[:10]}{'...' if len(missing) > 10 else ''}",
        )
    labels = pd.read_csv(counterfactual_path, low_memory=False)
    if labels.empty:
        raise RuntimeError(f"no counterfactual plans in {counterfactual_path}")
    if labels["plan_id"].duplicated().any():
        raise RuntimeError("counterfactual plan_id values must be unique")

    keep_event = [
        "plan_id",
        "ts_ns",
        "symbol",
        "family",
        "side",
        "scenario_path",
        "model_id",
        "model_status",
        "ml_mode",
        "ml_raw_probability",
        "ml_target_probability",
        "ml_tree_probability_std",
        "ml_required_probability",
        "ml_expected_net_r",
        "ml_model_accepted",
        "ml_baseline_eligible",
        "ml_win_net_r",
        "ml_loss_net_r",
        "ml_break_even_probability",
        *feature_columns,
    ]
    keep_event = [column for column in keep_event if column in feature_events.columns]
    outcome_columns = [
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
    outcome_columns = [column for column in outcome_columns if column in labels.columns]
    merged = feature_events[keep_event].merge(
        labels[outcome_columns],
        on="plan_id",
        how="inner",
        validate="one_to_one",
    )
    if merged.empty:
        raise RuntimeError("no plan identities matched between ML features and labels")

    merged["label"] = merged["counterfactual_outcome"].map(
        {
            "TARGET_FIRST": 1.0,
            "STOP_FIRST": 0.0,
            "AMBIGUOUS_SAME_MINUTE": 0.0,
        },
    )
    merged["event_time_ns"] = pd.to_numeric(merged["ts_ns"], errors="raise").astype("int64")
    resolution = pd.to_datetime(
        merged["counterfactual_resolution_time"],
        utc=True,
        errors="coerce",
    )
    merged["label_end_ns"] = resolution.astype("int64", errors="ignore")
    # pandas uses int64 minimum for NaT; replace it explicitly.
    nat_value = pd.NaT.value
    merged.loc[merged["label_end_ns"] == nat_value, "label_end_ns"] = pd.NA
    merged["event_date"] = pd.to_datetime(
        merged["event_time_ns"],
        unit="ns",
        utc=True,
    ).dt.strftime("%Y-%m-%d")

    for name, column in zip(FEATURE_NAMES, feature_columns, strict=True):
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(
            FEATURE_DEFAULTS[name],
        )
    if not keep_unresolved:
        merged = merged[merged["label"].notna()].copy()
    merged = merged.sort_values(["event_time_ns", "symbol", "plan_id"], kind="mergesort")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False)

    resolved = merged[merged["label"].notna()]
    summary: dict[str, Any] = {
        "rows": int(len(merged)),
        "resolved_rows": int(len(resolved)),
        "positive_target_first": int((resolved["label"] == 1.0).sum()),
        "negative_stop_or_ambiguous": int((resolved["label"] == 0.0).sum()),
        "feature_count": len(FEATURE_NAMES),
        "feature_names": list(FEATURE_NAMES),
        "label_policy": LABEL_POLICY,
        "events_path": str(events_path),
        "events_sha256": _sha256(events_path),
        "counterfactual_path": str(counterfactual_path),
        "counterfactual_sha256": _sha256(counterfactual_path),
        "output_path": str(output_path),
        "by_symbol": {
            str(symbol): {
                "rows": int(len(group)),
                "target_first_rate": None if group.empty else float(group["label"].mean()),
            }
            for symbol, group in resolved.groupby("symbol", sort=True)
        },
        "by_family": {
            str(family): {
                "rows": int(len(group)),
                "target_first_rate": None if group.empty else float(group["label"].mean()),
            }
            for family, group in resolved.groupby("family", sort=True, dropna=False)
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
    output = args.output or args.run_output / "ml1_dataset.csv"
    summary = args.summary or args.run_output / "ml1_dataset_summary.json"
    result = build_dataset(
        events_path=events,
        counterfactual_path=counterfactual,
        output_path=output,
        summary_path=summary,
        keep_unresolved=args.keep_unresolved,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
