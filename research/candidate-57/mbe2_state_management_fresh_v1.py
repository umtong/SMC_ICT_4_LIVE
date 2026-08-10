#!/usr/bin/env python3
"""Fresh MBE2 single-versus-breadth state-management experiment."""
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
    "candidate57_mbe2_state_management_campaign", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable MBE2 campaign: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MODULE.ROOT = ROOT
MODULE.REUSED = ROOT / "research" / "candidate-51"
MODULE.BASE_CONFIG = MODULE.REUSED / "config.json"
MODULE.WORK = ROOT / ".work" / "candidate-57-mbe2-state-management-fresh-v1"
MODULE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-mbe2-state-management-fresh-v1"
MODULE.EVIDENCE = HERE / "evidence" / "mbe2-state-management-fresh-v1"
MODULE.CACHE = ROOT / ".cache" / "candidate-57-mbe2-state-management-fresh-v1"

STAGE = MODULE.Stage(
    key="fresh_state_management_v1",
    name="untouched-july-2026-14d",
    start=date(2026, 7, 1),
    end=date(2026, 7, 14),
)

HORIZONS: dict[str, dict[str, int]] = {
    "roi_open_control": {
        "mbe_single_max_hold_minutes": 10_080,
        "mbe_breadth_max_hold_minutes": 10_080,
    },
    "roi_h240_control": {
        "mbe_single_max_hold_minutes": 240,
        "mbe_breadth_max_hold_minutes": 240,
    },
    "roi_state_hybrid": {
        "mbe_single_max_hold_minutes": 240,
        "mbe_breadth_max_hold_minutes": 10_080,
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
            "fresh state-dependent holding policy; source entry, arbitration "
            "and ROI ladder unchanged"
        ),
        source_faithful=False,
    )
    for name in HORIZONS
)
MODULE.STAGES = (STAGE,)
MODULE.VARIANTS = VARIANTS
MODULE.VARIANT_BY_NAME = {variant.name: variant for variant in VARIANTS}

_ORIGINAL_BUILD_CONFIG = MODULE.build_config


def build_config(stage: Any, variant: Any) -> Path:
    path = _ORIGINAL_BUILD_CONFIG(stage, variant)
    config = json.loads(path.read_text(encoding="utf-8"))
    config["strategy"].update(HORIZONS[variant.name])
    path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


MODULE.build_config = build_config


def compact_case(row: dict[str, Any]) -> dict[str, Any]:
    anatomy = row.get("trade_anatomy", {})
    return {
        "mechanics_ok": row.get("mechanics_ok"),
        "mechanical_reasons": row.get("mechanical_reasons"),
        "continuous_account": row.get("continuous_account"),
        "overall": anatomy.get("overall"),
        "by_exit_reason": anatomy.get("by_exit_reason"),
        "by_signal_collision": anatomy.get("by_signal_collision"),
        "descriptive_observations": row.get("descriptive_observations"),
        "trade_records": row.get("trade_records"),
    }


def metric(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def delta(
    results: dict[str, dict[str, Any]], left: str, right: str
) -> dict[str, Any]:
    left_account = results[left].get("continuous_account") or {}
    right_account = results[right].get("continuous_account") or {}
    left_overall = results[left].get("trade_anatomy", {}).get("overall", {})
    right_overall = results[right].get("trade_anatomy", {}).get("overall", {})
    return {
        "left": left,
        "right": right,
        "delta_geometric_daily_growth": metric(
            right_account, "geometric_daily_growth"
        )
        - metric(left_account, "geometric_daily_growth"),
        "delta_total_return": metric(right_account, "total_return")
        - metric(left_account, "total_return"),
        "delta_max_drawdown": metric(right_account, "max_drawdown")
        - metric(left_account, "max_drawdown"),
        "delta_completed_positions": int(
            right_overall.get("completed_positions") or 0
        )
        - int(left_overall.get("completed_positions") or 0),
        "delta_expectancy_r": metric(right_overall, "expectancy_r")
        - metric(left_overall, "expectancy_r"),
        "delta_profit_factor": metric(right_overall, "profit_factor")
        - metric(left_overall, "profit_factor"),
        "delta_average_hold_minutes": metric(
            right_overall, "average_hold_minutes"
        )
        - metric(left_overall, "average_hold_minutes"),
    }


def main() -> int:
    freeze = HERE / "MBE2_STATE_MANAGEMENT_FRESH_V1_FREEZE.md"
    if not freeze.is_file():
        raise RuntimeError("frozen MBE2 state-management specification missing")
    for path in (MODULE.WORK, MODULE.ARTIFACTS, MODULE.EVIDENCE, MODULE.CACHE):
        path.mkdir(parents=True, exist_ok=True)

    results = {variant.name: MODULE.run_case(STAGE, variant) for variant in VARIANTS}
    comparison: dict[str, Any] = {
        "experiment": "candidate-57-mbe2-state-management-fresh-v1",
        "binary_gate": False,
        "fresh_interval_consumed": True,
        "stage": asdict(STAGE) | {
            "days": STAGE.days,
            "warmup_days": MODULE.WARMUP_DAYS,
        },
        "research_contract": {
            "state_known_before_entry": True,
            "single_state": "mbe_collision_competitors == 0",
            "breadth_state": "mbe_collision_competitors >= 1",
            "unchanged": [
                "public completed-5m MBE2 short RSI70 down-cross entry",
                "TEMA above Bollinger middle and falling",
                "source cross-symbol arbitration",
                "6.46x source profit-ratio semantics",
                "public ROI ladder with trailing disabled",
                "all source episodes remain eligible",
                "current-NAV 3% planned-loss sizing",
                "realistic project costs and NautilusTrader one-slot account",
            ],
        },
        "declared_horizons": HORIZONS,
        "cells": {name: compact_case(row) for name, row in results.items()},
        "policy_deltas": {
            "all_h240_vs_open": delta(
                results, "roi_open_control", "roi_h240_control"
            ),
            "hybrid_vs_open": delta(
                results, "roi_open_control", "roi_state_hybrid"
            ),
            "hybrid_vs_all_h240": delta(
                results, "roi_h240_control", "roi_state_hybrid"
            ),
        },
    }
    MODULE.dump(MODULE.EVIDENCE / "comparison.json", comparison)

    lines = [
        "# MBE2 state-dependent management — fresh result",
        "",
        "Single and breadth states are observed before entry. This is a causal "
        "management comparison, not a binary gate.",
        "",
        "| cell | trades | win rate | expectancy R | PF | geo/day | total return | MDD | avg hold min |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in VARIANTS:
        row = results[variant.name]
        account = row.get("continuous_account") or {}
        overall = row.get("trade_anatomy", {}).get("overall", {})
        lines.append(
            "| "
            + " | ".join(
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
            )
            + " |"
        )
    (MODULE.EVIDENCE / "RESULT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "stage": comparison["stage"],
                "policy_deltas": comparison["policy_deltas"],
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
