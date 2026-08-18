#!/usr/bin/env python3
"""Enrich the causal ML3v3 plan dataset with integrated auction context."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from build_ml3v3_dataset import build_dataset as build_base_dataset
from features_integrated import (
    FEATURE_DEFAULTS,
    FEATURE_NAMES,
    INTEGRATED_FEATURE_NAMES,
    integrated_context_features,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-output", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--counterfactual", type=Path)
    parser.add_argument("--base-dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--period-name")
    return parser.parse_args()


def _side_value(value: Any) -> str:
    text = str(getattr(value, "name", value)).upper()
    if text.endswith("LONG") or text == "BUY":
        return "LONG"
    if text.endswith("SHORT") or text == "SELL":
        return "SHORT"
    raise ValueError(f"unknown side {value!r}")


def build_integrated_dataset(
    *,
    run_output: Path,
    events_path: Path,
    counterfactual_path: Path,
    base_path: Path,
    output_path: Path,
    summary_path: Path,
    period_name: str | None,
) -> dict[str, Any]:
    if not base_path.exists():
        build_base_dataset(
            events_path=events_path,
            counterfactual_path=counterfactual_path,
            output_path=base_path,
            summary_path=base_path.with_name(base_path.stem + "_summary.json"),
        )
    base = pd.read_csv(base_path, low_memory=False)
    events = pd.read_csv(events_path, low_memory=False)
    if "scenario_kind" not in events.columns or "plan_id" not in events.columns:
        raise RuntimeError("decision events do not contain integrated transition identity")
    context = events[
        events["scenario_kind"].astype(str).eq("integrated_plan_context")
    ].copy()
    if context.empty:
        raise RuntimeError("no integrated_plan_context transitions were recorded")
    ordering = [name for name in ("plan_id", "ts_ns", "event_time_ns") if name in context]
    context = context.sort_values(ordering, kind="mergesort").drop_duplicates(
        "plan_id", keep="last"
    )
    required = [
        "plan_id",
        "structure_60",
        "structure_15",
        "channel_confluence",
        "causal_noise_buffer",
    ]
    missing = [name for name in required if name not in context.columns]
    if missing:
        raise RuntimeError(f"integrated context is missing columns {missing}")
    keep = required + [
        name
        for name in (
            "signed_direction_score",
            "structural_alignment",
            "source_edge_score",
            "common_factor_alignment",
            "channel_60_direction",
            "channel_60_quality",
            "channel_60_position",
            "channel_15_direction",
            "channel_15_quality",
            "channel_15_position",
        )
        if name in context.columns
    ]
    merged = base.merge(
        context[keep],
        on="plan_id",
        how="left",
        validate="one_to_one",
    )
    matched = merged["structure_60"].notna() & merged["structure_15"].notna()
    if not bool(matched.any()):
        raise RuntimeError("no resolved plan matched its integrated context")

    values: list[dict[str, float]] = []
    for row in merged.itertuples(index=False):
        available = pd.notna(getattr(row, "structure_60")) and pd.notna(
            getattr(row, "structure_15")
        )
        values.append(
            integrated_context_features(
                side=_side_value(getattr(row, "side")),
                entry=float(getattr(row, "entry")),
                stop=float(getattr(row, "stop")),
                structure_60=getattr(row, "structure_60") if available else 0.0,
                structure_15=getattr(row, "structure_15") if available else 0.0,
                channel_confluence=(
                    getattr(row, "channel_confluence") if available else 0.0
                ),
                causal_noise_buffer=(
                    getattr(row, "causal_noise_buffer") if available else 0.0
                ),
                available=available,
            )
        )
    for name in INTEGRATED_FEATURE_NAMES:
        merged[f"mlf_{name}"] = [item[name] for item in values]
    for name in FEATURE_NAMES:
        column = f"mlf_{name}"
        if column not in merged.columns:
            raise RuntimeError(f"integrated dataset missing feature {column}")
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(
            FEATURE_DEFAULTS[name]
        )
    if period_name is not None:
        merged["development_period"] = str(period_name)
    elif "development_period" not in merged.columns:
        merged["development_period"] = run_output.name
    merged = merged.sort_values(
        ["event_time_ns", "event_group_id", "plan_id"], kind="mergesort"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False)

    resolved = merged[merged["label"].isin([0, 1])]
    summary: dict[str, Any] = {
        "rows": int(len(merged)),
        "matched_integrated_context": int(matched.sum()),
        "missing_integrated_context": int((~matched).sum()),
        "event_groups": int(merged["event_group_id"].nunique()),
        "target_first_rate": float(resolved["label"].mean()),
        "period": str(merged["development_period"].iloc[0]),
        "feature_count": len(FEATURE_NAMES),
        "integrated_feature_count": len(INTEGRATED_FEATURE_NAMES),
        "output": str(output_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    args = parse_args()
    run_output = args.run_output
    events = args.events or run_output / "decision_events.csv"
    counterfactual = args.counterfactual or run_output / "counterfactual_plans.csv"
    base = args.base_dataset or run_output / "ml3v3_dataset.csv"
    output = args.output or run_output / "integrated_dataset.csv"
    summary = args.summary or run_output / "integrated_dataset_summary.json"
    result = build_integrated_dataset(
        run_output=run_output,
        events_path=events,
        counterfactual_path=counterfactual,
        base_path=base,
        output_path=output,
        summary_path=summary,
        period_name=args.period_name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
