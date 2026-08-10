#!/usr/bin/env python3
"""Fresh 2x2 MBE2 collision-confirmation × day-trade-horizon experiment."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE = HERE / "forensic_sources" / "mbe2_campaign.py"
SPEC = importlib.util.spec_from_file_location(
    "candidate57_mbe2_collision_daytrade_campaign", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable MBE2 campaign: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MODULE.ROOT = ROOT
MODULE.REUSED = ROOT / "research" / "candidate-51"
MODULE.BASE_CONFIG = MODULE.REUSED / "config.json"
MODULE.WORK = ROOT / ".work" / "candidate-57-mbe2-collision-daytrade-fresh-v1"
MODULE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-mbe2-collision-daytrade-fresh-v1"
MODULE.EVIDENCE = HERE / "evidence" / "mbe2-collision-daytrade-fresh-v1"
MODULE.CACHE = ROOT / ".cache" / "candidate-57-mbe2-collision-daytrade-fresh-v1"

STAGE = MODULE.Stage(
    key="fresh_collision_daytrade_v1",
    name="untouched-june-2026-7d",
    start=date(2026, 6, 1),
    end=date(2026, 6, 7),
)

CELLS: dict[str, dict[str, int]] = {
    "short_roi_control": {
        "mbe_min_actionable_candidates": 1,
        "mbe_daytrade_max_hold_minutes": 10_080,
    },
    "short_roi_h240": {
        "mbe_min_actionable_candidates": 1,
        "mbe_daytrade_max_hold_minutes": 240,
    },
    "short_roi_collision": {
        "mbe_min_actionable_candidates": 2,
        "mbe_daytrade_max_hold_minutes": 10_080,
    },
    "short_roi_collision_h240": {
        "mbe_min_actionable_candidates": 2,
        "mbe_daytrade_max_hold_minutes": 240,
    },
}

VARIANTS = tuple(
    MODULE.Variant(
        name=name,
        side="short",
        leverage=6.46,
        management="roi_only",
        roi_114=0.11,
        component_role=(
            "fresh causal 2x2 cell; source entry and ROI ladder unchanged"
        ),
        source_faithful=False,
    )
    for name in CELLS
)
MODULE.STAGES = (STAGE,)
MODULE.VARIANTS = VARIANTS
MODULE.VARIANT_BY_NAME = {variant.name: variant for variant in VARIANTS}

_ORIGINAL_BUILD_CONFIG = MODULE.build_config


def build_config(stage: Any, variant: Any) -> Path:
    path = _ORIGINAL_BUILD_CONFIG(stage, variant)
    config = json.loads(path.read_text(encoding="utf-8"))
    config["strategy"].update(CELLS[variant.name])
    path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


MODULE.build_config = build_config


def compact_case(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "mechanics_ok": row.get("mechanics_ok"),
        "mechanical_reasons": row.get("mechanical_reasons"),
        "continuous_account": row.get("continuous_account"),
        "overall": row.get("trade_anatomy", {}).get("overall"),
        "by_exit_reason": row.get("trade_anatomy", {}).get("by_exit_reason"),
        "by_signal_collision": row.get("trade_anatomy", {}).get(
            "by_signal_collision"
        ),
        "descriptive_observations": row.get("descriptive_observations"),
        "trade_records": row.get("trade_records"),
    }


def metric(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main() -> int:
    for path in (MODULE.WORK, MODULE.ARTIFACTS, MODULE.EVIDENCE, MODULE.CACHE):
        path.mkdir(parents=True, exist_ok=True)

    results = {variant.name: MODULE.run_case(STAGE, variant) for variant in VARIANTS}
    control = results["short_roi_control"]
    control_account = control.get("continuous_account") or {}
    control_overall = control.get("trade_anatomy", {}).get("overall", {})
    comparison: dict[str, Any] = {
        "experiment": "candidate-57-mbe2-collision-daytrade-fresh-v1",
        "research_contract": {
            "binary_gate": False,
            "fresh_interval_consumed": True,
            "factors": {
                "cross_asset_confirmation": "at least 2 simultaneous source-actionable symbols before arbitration",
                "daytrade_horizon": "240 completed minutes vs source/open-ended control",
            },
            "unchanged": [
                "public completed-5m MBE2 short RSI70 down-cross entry",
                "TEMA above Bollinger middle and falling",
                "6.46x source profit-ratio semantics",
                "public ROI ladder",
                "current-NAV 3% planned-loss sizing",
                "realistic project costs and NautilusTrader one-slot account",
            ],
        },
        "stage": asdict(STAGE) | {"days": STAGE.days},
        "cells": {name: compact_case(row) for name, row in results.items()},
        "deltas_vs_control": {},
    }
    for name, row in results.items():
        account = row.get("continuous_account") or {}
        overall = row.get("trade_anatomy", {}).get("overall", {})
        comparison["deltas_vs_control"][name] = {
            "delta_geometric_daily_growth": metric(
                account, "geometric_daily_growth"
            )
            - metric(control_account, "geometric_daily_growth"),
            "delta_total_return": metric(account, "total_return")
            - metric(control_account, "total_return"),
            "delta_max_drawdown": metric(account, "max_drawdown")
            - metric(control_account, "max_drawdown"),
            "delta_completed_positions": int(
                overall.get("completed_positions") or 0
            )
            - int(control_overall.get("completed_positions") or 0),
            "delta_expectancy_r": metric(overall, "expectancy_r")
            - metric(control_overall, "expectancy_r"),
            "delta_profit_factor": metric(overall, "profit_factor")
            - metric(control_overall, "profit_factor"),
        }

    MODULE.dump(MODULE.EVIDENCE / "comparison.json", comparison)
    lines = [
        "# MBE2 collision confirmation × day-trade horizon — fresh result",
        "",
        "This is a four-cell causal factor map, not a binary gate.",
        "",
        "| cell | trades | win rate | expectancy R | PF | geo/day | total return | MDD | avg hold min |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in VARIANTS:
        row = results[variant.name]
        account = row.get("continuous_account") or {}
        overall = row.get("trade_anatomy", {}).get("overall", {})
        lines.append(
            "| " + " | ".join(
                [
                    variant.name,
                    str(overall.get("completed_positions")),
                    str(overall.get("win_rate")),
                    str(overall.get("expectancy_r")),
                    str(overall.get("profit_factor")),
                    str(account.get("geometric_daily_growth")),
                    str(account.get("total_return")),
                    str(account.get("max_drawdown")),
                    str(overall.get("average_hold_minutes")),
                ]
            ) + " |"
        )
    (MODULE.EVIDENCE / "RESULT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "stage": comparison["stage"],
                "deltas_vs_control": comparison["deltas_vs_control"],
                "mechanics": {
                    name: row.get("mechanics_ok") for name, row in results.items()
                },
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if all(row.get("mechanics_ok") for row in results.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
