#!/usr/bin/env python3
"""Run the two pre-frozen MBE2 short-management cells on a fresh interval."""
from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "forensic_sources" / "mbe2_campaign.py"
SPEC = importlib.util.spec_from_file_location(
    "candidate57_mbe2_short_fresh_campaign", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reused MBE2 anatomy campaign: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

stage = MODULE.Stage(
    key="fresh_short_management_v1",
    name="fresh-short-management-7d",
    start=date(2026, 5, 4),
    end=date(2026, 5, 10),
)
source = MODULE.Variant(
    name="short_avg646_source",
    side="short",
    leverage=6.46,
    management="source",
    roi_114=0.11,
    component_role="SHORT_SOURCE_MANAGEMENT_CONTROL",
    source_faithful=True,
)
roi_only = MODULE.Variant(
    name="short_avg646_roi_only",
    side="short",
    leverage=6.46,
    management="roi_only",
    roi_114=0.11,
    component_role="SHORT_ROI_ONLY_HYPOTHESIS",
    source_faithful=False,
)

MODULE.STAGES = (stage,)
MODULE.VARIANTS = (source, roi_only)
MODULE.VARIANT_BY_NAME = {variant.name: variant for variant in MODULE.VARIANTS}
MODULE.WORK = HERE.parent.parent / ".work" / "candidate-57-mbe2-short-management-fresh-v1"
MODULE.ARTIFACTS = HERE.parent.parent / "artifacts" / "candidate-57-mbe2-short-management-fresh-v1"
MODULE.EVIDENCE = HERE / "evidence" / "mbe2-short-management-fresh-v1"
MODULE.CACHE = HERE.parent.parent / ".cache" / "candidate-57-mbe2-short-management-fresh-v1"

# The generic two-period synthesis expects at least two stages. Reuse the exact
# case runner and write a one-stage causal comparison instead.
def main() -> int:
    for path in (MODULE.WORK, MODULE.ARTIFACTS, MODULE.EVIDENCE, MODULE.CACHE):
        path.mkdir(parents=True, exist_ok=True)
    results = {
        variant.name: MODULE.run_case(stage, variant)
        for variant in MODULE.VARIANTS
    }
    comparison = {
        "experiment": "candidate-57-mbe2-short-management-fresh-v1",
        "binary_gate": False,
        "fresh_interval_consumed": True,
        "stage": {
            "key": stage.key,
            "name": stage.name,
            "start": stage.start.isoformat(),
            "end": stage.end.isoformat(),
            "days": stage.days,
            "warmup_days": MODULE.WARMUP_DAYS,
        },
        "source_contract": {
            "entry": "public completed-5m RSI70 down-cross with falling TEMA above BB middle",
            "side": "short only",
            "effective_leverage": 6.46,
            "one_global_slot": 1,
            "risk_fraction": 0.03,
            "engine": "NautilusTrader BacktestNode",
        },
        "cells": {
            name: {
                "mechanics_ok": row.get("mechanics_ok"),
                "mechanical_reasons": row.get("mechanical_reasons"),
                "continuous_account": row.get("continuous_account"),
                "trade_anatomy": row.get("trade_anatomy"),
                "descriptive_observations": row.get("descriptive_observations"),
                "trade_records": row.get("trade_records"),
            }
            for name, row in results.items()
        },
    }
    MODULE.dump(MODULE.EVIDENCE / "comparison.json", comparison)
    lines = [
        "# MBE2 short management fresh result",
        "",
        "This is a causal management comparison, not a pass/fail gate.",
        "",
        "| cell | completed positions | win rate | expectancy R | PF | geo/day | MDD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in MODULE.VARIANTS:
        row = results[variant.name]
        overall = row.get("trade_anatomy", {}).get("overall", {})
        account = row.get("continuous_account", {})
        pf = overall.get("profit_factor")
        lines.append(
            f"| {variant.name} | {overall.get('completed_positions')} | "
            f"{overall.get('win_rate')} | {overall.get('expectancy_r')} | "
            f"{pf} | {account.get('geometric_daily_growth')} | "
            f"{account.get('max_drawdown')} |"
        )
    (MODULE.EVIDENCE / "RESULT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(MODULE.json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False))
    return 0 if all(row.get("mechanics_ok") for row in results.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
