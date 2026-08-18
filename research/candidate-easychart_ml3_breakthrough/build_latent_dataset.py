#!/usr/bin/env python3
"""Build a causal dataset containing integrated and latent auction states."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from build_integrated_dataset import build_integrated_dataset
from features_latent import (
    FEATURE_DEFAULTS,
    FEATURE_NAMES,
    LATENT_FEATURE_NAMES,
    latent_context_features,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-output", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--period-name")
    return parser.parse_args()


def build_latent_dataset(
    *,
    run_output: Path,
    output_path: Path,
    summary_path: Path,
    period_name: str | None,
) -> dict[str, Any]:
    integrated_path = run_output / "integrated_dataset.csv"
    if not integrated_path.exists():
        build_integrated_dataset(
            run_output=run_output,
            events_path=run_output / "decision_events.csv",
            counterfactual_path=run_output / "counterfactual_plans.csv",
            base_path=run_output / "ml3v3_dataset.csv",
            output_path=integrated_path,
            summary_path=run_output / "integrated_dataset_summary.json",
            period_name=period_name,
        )
    base = pd.read_csv(integrated_path, low_memory=False)
    events = pd.read_csv(run_output / "decision_events.csv", low_memory=False)
    context = events[
        events.get("scenario_kind", pd.Series(index=events.index, dtype=str))
        .astype(str)
        .eq("latent_plan_context")
    ].copy()
    if context.empty:
        raise RuntimeError("no latent_plan_context transitions were recorded")
    ordering = [name for name in ("plan_id", "ts_ns", "event_time_ns") if name in context]
    context = context.sort_values(ordering, kind="mergesort").drop_duplicates(
        "plan_id", keep="last"
    )
    required = [
        "plan_id",
        "regime",
        "draw_alignment",
        "draw_balance",
        "trend_60_alignment",
        "trend_15_alignment",
        "factor_alignment",
        "location_alignment",
    ]
    missing = [name for name in required if name not in context]
    if missing:
        raise RuntimeError(f"latent context is missing columns {missing}")
    merged = base.merge(
        context[required], on="plan_id", how="left", validate="one_to_one"
    )
    matched = merged["regime"].notna()
    if not bool(matched.any()):
        raise RuntimeError("no resolved plan matched latent context")
    values: list[dict[str, float]] = []
    for row in merged.itertuples(index=False):
        available = pd.notna(getattr(row, "regime"))
        values.append(
            latent_context_features(
                side=getattr(row, "side"),
                scenario_path=getattr(row, "scenario_path"),
                regime=getattr(row, "regime") if available else "MIXED",
                draw_alignment=getattr(row, "draw_alignment") if available else 0.0,
                draw_balance=getattr(row, "draw_balance") if available else 0.0,
                trend_60_alignment=(
                    getattr(row, "trend_60_alignment") if available else 0.0
                ),
                trend_15_alignment=(
                    getattr(row, "trend_15_alignment") if available else 0.0
                ),
                factor_alignment=(
                    getattr(row, "factor_alignment") if available else 0.0
                ),
                location_alignment=(
                    getattr(row, "location_alignment") if available else 0.0
                ),
                available=available,
            )
        )
    for name in LATENT_FEATURE_NAMES:
        merged[f"mlf_{name}"] = [item[name] for item in values]
    for name in FEATURE_NAMES:
        column = f"mlf_{name}"
        if column not in merged.columns:
            raise RuntimeError(f"latent dataset missing feature {column}")
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(
            FEATURE_DEFAULTS[name]
        )
    if period_name is not None:
        merged["development_period"] = str(period_name)
    merged = merged.sort_values(
        ["event_time_ns", "event_group_id", "plan_id"], kind="mergesort"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False)
    summary = {
        "rows": int(len(merged)),
        "matched_latent_context": int(matched.sum()),
        "missing_latent_context": int((~matched).sum()),
        "event_groups": int(merged["event_group_id"].nunique()),
        "target_first_rate": float(merged["label"].mean()),
        "feature_count": len(FEATURE_NAMES),
        "latent_feature_count": len(LATENT_FEATURE_NAMES),
        "period": str(merged["development_period"].iloc[0]),
        "output": str(output_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    args = parse_args()
    output = args.output or args.run_output / "latent_dataset.csv"
    summary = args.summary or args.run_output / "latent_dataset_summary.json"
    result = build_latent_dataset(
        run_output=args.run_output,
        output_path=output,
        summary_path=summary,
        period_name=args.period_name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
