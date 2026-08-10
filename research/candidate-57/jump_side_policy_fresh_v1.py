#!/usr/bin/env python3
"""Fresh 2x2 test of jump side state and peer-taker routing policy."""
from __future__ import annotations

from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE = HERE / "jump_taker_alignment_fresh_campaign.py"
SPEC = importlib.util.spec_from_file_location(
    "candidate57_jump_side_policy_fresh_base", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable jump campaign: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MODULE.WORK = ROOT / ".work" / "candidate-57-jump-side-policy-fresh-v1"
MODULE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-jump-side-policy-fresh-v1"
MODULE.EVIDENCE = HERE / "evidence" / "jump-side-policy-fresh-v1"
MODULE.CACHE = ROOT / ".cache" / "candidate-57-jump-side-policy-fresh-v1"
MODULE.METRICS = MODULE.WORK / "binance_metrics_2026-07-11_2026-07-28.json"
MODULE.START = date(2026, 7, 15)
MODULE.END = date(2026, 7, 28)
MODULE.DAYS = (MODULE.END - MODULE.START).days + 1

CELLS: dict[str, dict[str, str]] = {
    "conditional_both": {
        "filter": "source_without_taker_filter",
        "arbitration": "taker_conditional",
        "side": "both",
    },
    "conditional_short_only": {
        "filter": "source_without_taker_filter",
        "arbitration": "taker_conditional",
        "side": "short_only",
    },
    "aligned_max_both": {
        "filter": "peer_taker_alignment_3of4",
        "arbitration": "source_max_z",
        "side": "both",
    },
    "aligned_max_short_only": {
        "filter": "peer_taker_alignment_3of4",
        "arbitration": "source_max_z",
        "side": "short_only",
    },
}


def download_sidecar() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2026-07-11",
        "--end",
        MODULE.END.isoformat(),
        "--output",
        str(MODULE.METRICS),
        "--cache",
        str(MODULE.CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def run_cell(cell: str) -> int:
    output = MODULE.ARTIFACTS / cell
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(MODULE.C51 / "launch.py"),
        "--config",
        str(MODULE.config(cell)),
        "--start",
        MODULE.START.isoformat(),
        "--end",
        MODULE.END.isoformat(),
        "--cache",
        str(MODULE.CACHE / "bars"),
        "--output",
        str(output),
        "--workspace",
        str(MODULE.WORK / cell / "workspace"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(MODULE.C51)
    env["C57_JUMP_TAKER_FILTER_MODE"] = CELLS[cell]["filter"]
    env["C57_JUMP_ARBITRATION_MODE"] = CELLS[cell]["arbitration"]
    env["C57_JUMP_SIDE_MODE"] = CELLS[cell]["side"]
    env["C57_JUMP_TAKER_METRICS_PATH"] = str(MODULE.METRICS)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def delta(
    results: dict[str, dict[str, Any]], left: str, right: str
) -> dict[str, Any]:
    left_account = results[left].get("actual_account") or {}
    right_account = results[right].get("actual_account") or {}
    return {
        "left": left,
        "right": right,
        "delta_trades": int(results[right].get("actual_completed_trades") or 0)
        - int(results[left].get("actual_completed_trades") or 0),
        "delta_geometric_daily_growth": number(
            right_account.get("geometric_daily_growth")
        )
        - number(left_account.get("geometric_daily_growth")),
        "delta_total_return": number(right_account.get("total_return"))
        - number(left_account.get("total_return")),
        "delta_max_drawdown": number(right_account.get("max_drawdown"))
        - number(left_account.get("max_drawdown")),
        "delta_profit_factor": number(right_account.get("profit_factor"))
        - number(left_account.get("profit_factor")),
    }


def main() -> int:
    freeze = HERE / "JUMP_SIDE_POLICY_FRESH_V1_FREEZE.md"
    if not freeze.is_file():
        raise RuntimeError("fresh jump side-policy freeze is missing")
    for path in (MODULE.WORK, MODULE.ARTIFACTS, MODULE.CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if MODULE.EVIDENCE.exists():
        shutil.rmtree(MODULE.EVIDENCE)
    MODULE.EVIDENCE.mkdir(parents=True, exist_ok=True)

    data_status = download_sidecar()
    process_status = data_status
    results: dict[str, dict[str, Any]] = {}
    for cell in CELLS:
        code = run_cell(cell) if data_status == 0 else 1
        process_status = process_status or code
        result = MODULE.analyze_cell(cell, code)
        result["declared_policy"] = CELLS[cell]
        results[cell] = result

    comparison = {
        "experiment": "candidate-57-jump-side-policy-fresh-v1",
        "binary_gate": False,
        "fresh_interval_consumed": True,
        "interval": [MODULE.START.isoformat(), MODULE.END.isoformat()],
        "cells": results,
        "factor_deltas": {
            "short_only_under_conditional": delta(
                results, "conditional_both", "conditional_short_only"
            ),
            "short_only_under_aligned_max": delta(
                results, "aligned_max_both", "aligned_max_short_only"
            ),
            "aligned_only_for_both_sides": delta(
                results, "conditional_both", "aligned_max_both"
            ),
            "aligned_only_for_short_side": delta(
                results, "conditional_short_only", "aligned_max_short_only"
            ),
        },
        "unchanged": [
            "completed 4h source jump >=2 sigma",
            "prior-only volatility window 18",
            "whole-impulse structural stop",
            "240-minute source horizon",
            "transient 0.4R arm and 1.0R escape management",
            "current-NAV 3% planned-loss sizing",
            "realistic project costs and NautilusTrader one-slot account",
        ],
    }
    MODULE.dump(MODULE.EVIDENCE / "comparison.json", comparison)

    lines = [
        "# Jump direction × market-state policy — fresh result",
        "",
        "This is a four-cell causal factor map. Simultaneous symbols at one "
        "completed four-hour boundary remain one market event.",
        "",
        "| cell | trades | W/L | PF | geo/day | total return | MDD | accepted boundaries |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in CELLS:
        result = results[cell]
        account = result.get("actual_account") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    cell,
                    str(result.get("actual_completed_trades")),
                    f"{account.get('wins')}/{account.get('losses')}",
                    str(account.get("profit_factor")),
                    str(account.get("geometric_daily_growth")),
                    str(account.get("total_return")),
                    str(account.get("max_drawdown")),
                    str(result.get("accepted_independent_boundaries")),
                ]
            )
            + " |"
        )
    (MODULE.EVIDENCE / "RESULT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False))

    if process_status != 0:
        return int(process_status)
    for result in results.values():
        if not result.get("produced"):
            return 1
        validity = result.get("end_validity") or {}
        if validity.get("no_open_positions_at_end") is False:
            return 2
        if validity.get("no_active_orders_at_end") is False:
            return 2
        if validity.get("no_global_position_violation") is False:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
