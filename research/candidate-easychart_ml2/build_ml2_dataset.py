#!/usr/bin/env python3
"""Build one identity-safe ML2 learning table from a completed research run.

The strategy emits one ``ml2_plan`` row when a deterministic EasyChart scenario
has already frozen entry, stop and target.  Future bars are consulted only by
the post-run counterfactual harvester.  This module performs an exact one-to-one
join and does not manufacture labels, rebalance outcomes, target a win rate or
encode any user-supplied example as a preferred trade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ml2_features import FEATURE_DEFAULTS, FEATURE_NAMES


LABEL_POLICY = (
    "TARGET_FIRST=1; STOP_FIRST=0; AMBIGUOUS_SAME_MINUTE=0; "
    "UNRESOLVED_HAS_NO_BINARY_LABEL; OBSERVED_OUTCOME_R_USES_THE_SAME_RUNTIME_"
    "WIN_OR_LOSS_ECONOMICS_RECORDED_BEFORE_ENTRY"
)
IDENTITY_POLICY = (
    "EXACT_ONE_TO_ONE_PLAN_ID_JOIN; CAUSAL_EVENT_GROUPS_ARE_SYMBOL_NAMESPACED; "
    "NO_DUPLICATE_PLAN_OR_SILENT_ROW_OVERWRITE"
)


def sha256_file(path: Path) -> str:
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
    parser.add_argument(
        "--keep-unresolved",
        action="store_true",
        help="Preserve right-censored rows for inspection; they remain unlabeled.",
    )
    return parser.parse_args()


def _require_columns(frame: pd.DataFrame, required: Iterable[str], *, source: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise RuntimeError(f"{source} missing required columns: {missing[:30]}")


def _assert_unique(frame: pd.DataFrame, column: str, *, source: str) -> None:
    missing = frame[column].isna() | (frame[column].astype(str).str.len() == 0)
    if missing.any():
        raise RuntimeError(f"{source} contains {int(missing.sum())} missing {column} values")
    duplicate = frame[frame[column].duplicated(keep=False)]
    if duplicate.empty:
        return
    sample_columns = [
        name
        for name in (
            column,
            "ts_ns",
            "symbol",
            "family",
            "side",
            "counterfactual_outcome",
        )
        if name in duplicate.columns
    ]
    raise RuntimeError(
        f"{source} contains duplicate {column} identities:\n"
        + duplicate[sample_columns].head(40).to_string(index=False),
    )


def _safe_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _group_id(frame: pd.DataFrame) -> pd.Series:
    causal = _safe_text(frame["causal_event_id"])
    fallback = _safe_text(frame["plan_id"])
    identity = causal.where(causal.str.len() > 0, fallback)
    return _safe_text(frame["symbol"]) + "|" + identity


def _descriptive_groups(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, group in frame.groupby(column, sort=True, dropna=False):
        resolved = group[group["label"].notna()]
        observed = pd.to_numeric(resolved["observed_outcome_net_r"], errors="coerce")
        output["<NA>" if pd.isna(key) else str(key)] = {
            "rows": int(len(group)),
            "resolved_rows": int(len(resolved)),
            "target_first": int((resolved["label"] == 1.0).sum()),
            "stop_or_ambiguous": int((resolved["label"] == 0.0).sum()),
            "target_first_rate": None if resolved.empty else float(resolved["label"].mean()),
            "mean_observed_outcome_net_r": None
            if observed.dropna().empty
            else float(observed.mean()),
        }
    return output


def build_dataset(
    *,
    events_path: Path,
    counterfactual_path: Path,
    output_path: Path,
    summary_path: Path,
    keep_unresolved: bool = False,
) -> dict[str, Any]:
    events = pd.read_csv(events_path, low_memory=False)
    _require_columns(events, ("kind", "plan_id"), source=str(events_path))
    feature_events = events[events["kind"] == "ml2_plan"].copy()
    if feature_events.empty:
        raise RuntimeError(f"no kind=ml2_plan rows in {events_path}")
    _assert_unique(feature_events, "plan_id", source="ML2 feature events")

    feature_columns = [f"ml2f_{name}" for name in FEATURE_NAMES]
    required_events = {
        "plan_id",
        "causal_event_id",
        "ts_ns",
        "symbol",
        "family",
        "side",
        "scenario_path",
        "ml2_causal_family",
        "ml2_win_net_r",
        "ml2_loss_net_r",
        "ml2_required_log_probability",
        *feature_columns,
    }
    _require_columns(feature_events, required_events, source="ML2 feature events")

    labels = pd.read_csv(counterfactual_path, low_memory=False)
    if labels.empty:
        raise RuntimeError(f"no counterfactual plans in {counterfactual_path}")
    _require_columns(
        labels,
        (
            "plan_id",
            "counterfactual_outcome",
            "counterfactual_resolution_time",
            "counterfactual_minutes_to_resolution",
        ),
        source=str(counterfactual_path),
    )
    _assert_unique(labels, "plan_id", source="counterfactual labels")

    preferred_event_columns = [
        "plan_id",
        "causal_event_id",
        "ts_ns",
        "symbol",
        "family",
        "side",
        "scenario_path",
        "ml2_causal_family",
        "model_id",
        "model_status",
        "ml_mode",
        "ml2_raw_probability",
        "ml2_target_probability",
        "ml2_required_log_probability",
        "ml2_arithmetic_break_even_probability",
        "ml2_expected_net_r",
        "ml2_expected_log_growth",
        "ml2_model_accepted",
        "ml2_decision_reason",
        "ml2_baseline_eligible",
        "ml2_setup_factor_side",
        "ml2_pre_response_factor_side",
        "ml2_win_net_r",
        "ml2_loss_net_r",
        "ml2_estimated_win_cost_r",
        "ml2_estimated_loss_cost_r",
        *feature_columns,
    ]
    event_columns = [name for name in preferred_event_columns if name in feature_events.columns]
    preferred_outcome_columns = [
        "plan_id",
        "counterfactual_outcome",
        "counterfactual_resolution_time",
        "counterfactual_minutes_to_resolution",
        "counterfactual_stop_time",
        "counterfactual_target_time",
        "counterfactual_mfe_r",
        "counterfactual_mae_r",
        "counterfactual_net_r_conservative",
        "counterfactual_target_net_r",
        "counterfactual_stop_net_r",
        "post_cost_reward_risk",
        "post_cost_break_even_target_probability",
        "risk_bps",
        "target_bps",
        "risk_in_prior_sigma",
        "target_in_prior_sigma",
        "risk_in_prior_range",
        "target_in_prior_range",
    ]
    outcome_columns = [name for name in preferred_outcome_columns if name in labels.columns]

    merged = feature_events[event_columns].merge(
        labels[outcome_columns],
        on="plan_id",
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    unmatched = merged[merged["_merge"] != "both"]
    if not unmatched.empty:
        raise RuntimeError(
            "ML2 feature events without counterfactual labels:\n"
            + unmatched[["plan_id", "symbol", "family"]].head(40).to_string(index=False),
        )
    merged = merged.drop(columns="_merge")

    outcome = _safe_text(merged["counterfactual_outcome"])
    merged["label"] = outcome.map(
        {
            "TARGET_FIRST": 1.0,
            "STOP_FIRST": 0.0,
            "AMBIGUOUS_SAME_MINUTE": 0.0,
        },
    )
    unknown_outcome = ~outcome.isin(
        ("TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "UNRESOLVED"),
    )
    if unknown_outcome.any():
        raise RuntimeError(
            "unknown counterfactual outcomes: "
            + repr(sorted(outcome[unknown_outcome].unique().tolist())),
        )

    merged["event_time_ns"] = pd.to_numeric(merged["ts_ns"], errors="raise").astype("int64")
    event_time = pd.to_datetime(merged["event_time_ns"], unit="ns", utc=True)
    merged["event_date"] = event_time.dt.strftime("%Y-%m-%d")
    merged["decision_bucket_id"] = merged["event_time_ns"].astype(str)
    merged["event_group_id"] = _group_id(merged)

    resolution = pd.to_datetime(
        merged["counterfactual_resolution_time"],
        utc=True,
        errors="coerce",
    )
    merged["label_end_ns"] = pd.Series(pd.NA, index=merged.index, dtype="Int64")
    resolved_time = resolution.notna()
    merged.loc[resolved_time, "label_end_ns"] = resolution.loc[resolved_time].astype("int64")
    resolved = merged["label"].notna()
    invalid_label_time = resolved & (
        merged["label_end_ns"].isna()
        | (merged["label_end_ns"].astype("Int64") <= merged["event_time_ns"])
    )
    if invalid_label_time.any():
        raise RuntimeError(
            "resolved plans must have label_end_ns strictly after event_time_ns:\n"
            + merged.loc[
                invalid_label_time,
                ["plan_id", "event_time_ns", "label_end_ns", "counterfactual_outcome"],
            ].head(40).to_string(index=False),
        )

    win_r = pd.to_numeric(merged["ml2_win_net_r"], errors="coerce")
    loss_r = pd.to_numeric(merged["ml2_loss_net_r"], errors="coerce")
    merged["observed_outcome_net_r"] = np.where(
        outcome == "TARGET_FIRST",
        win_r,
        np.where(outcome.isin(("STOP_FIRST", "AMBIGUOUS_SAME_MINUTE")), loss_r, np.nan),
    )
    invalid_economics = resolved & ~np.isfinite(
        pd.to_numeric(merged["observed_outcome_net_r"], errors="coerce"),
    )
    if invalid_economics.any():
        raise RuntimeError(
            "resolved plans have non-finite runtime-consistent outcome economics:\n"
            + merged.loc[
                invalid_economics,
                ["plan_id", "counterfactual_outcome", "ml2_win_net_r", "ml2_loss_net_r"],
            ].head(40).to_string(index=False),
        )

    for name, column in zip(FEATURE_NAMES, feature_columns, strict=True):
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(
            FEATURE_DEFAULTS[name],
        )

    all_rows = merged.copy()
    if not keep_unresolved:
        merged = merged[resolved].copy()
    merged = merged.sort_values(
        ["event_time_ns", "symbol", "plan_id"],
        kind="mergesort",
    ).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False)

    resolved_all = all_rows[all_rows["label"].notna()].copy()
    resolution_minutes = pd.to_numeric(
        resolved_all["counterfactual_minutes_to_resolution"],
        errors="coerce",
    )
    summary: dict[str, Any] = {
        "rows_written": int(len(merged)),
        "candidate_rows": int(len(all_rows)),
        "resolved_rows": int(len(resolved_all)),
        "unresolved_rows": int((all_rows["counterfactual_outcome"] == "UNRESOLVED").sum()),
        "target_first": int((resolved_all["label"] == 1.0).sum()),
        "stop_or_ambiguous": int((resolved_all["label"] == 0.0).sum()),
        "ambiguous_same_minute": int(
            (resolved_all["counterfactual_outcome"] == "AMBIGUOUS_SAME_MINUTE").sum(),
        ),
        "mean_observed_outcome_net_r": None
        if resolved_all.empty
        else float(pd.to_numeric(resolved_all["observed_outcome_net_r"]).mean()),
        "median_minutes_to_resolution": None
        if resolution_minutes.dropna().empty
        else float(resolution_minutes.median()),
        "feature_count": len(FEATURE_NAMES),
        "feature_names": list(FEATURE_NAMES),
        "label_policy": LABEL_POLICY,
        "identity_policy": IDENTITY_POLICY,
        "events_path": str(events_path),
        "events_sha256": sha256_file(events_path),
        "counterfactual_path": str(counterfactual_path),
        "counterfactual_sha256": sha256_file(counterfactual_path),
        "output_path": str(output_path),
        "output_sha256": sha256_file(output_path),
        "by_symbol": _descriptive_groups(all_rows, "symbol"),
        "by_family": _descriptive_groups(all_rows, "family"),
        "by_causal_family": _descriptive_groups(all_rows, "ml2_causal_family"),
        "by_side": _descriptive_groups(all_rows, "side"),
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
    output = args.output or args.run_output / "ml2_dataset.csv"
    summary = args.summary or args.run_output / "ml2_dataset_summary.json"
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
