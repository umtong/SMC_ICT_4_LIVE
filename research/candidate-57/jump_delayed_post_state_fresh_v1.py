#!/usr/bin/env python3
"""Fresh comparison of immediate and delayed post-cascade jump states."""
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
    "candidate57_jump_delayed_post_state_fresh_base", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable jump campaign: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MODULE.WORK = ROOT / ".work" / "candidate-57-jump-delayed-post-state-fresh-v1"
MODULE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-jump-delayed-post-state-fresh-v1"
MODULE.EVIDENCE = HERE / "evidence" / "jump-delayed-post-state-fresh-v1"
MODULE.CACHE = ROOT / ".cache" / "candidate-57-jump-delayed-post-state-fresh-v1"
MODULE.METRICS = MODULE.WORK / "binance_metrics_2026-07-25_2026-08-09.json"
MODULE.START = date(2026, 7, 29)
MODULE.END = date(2026, 8, 9)
MODULE.DAYS = (MODULE.END - MODULE.START).days + 1

CELLS: dict[str, dict[str, Any]] = {
    "immediate_control": {
        "mode": "immediate",
        "confirmation_minutes": 0,
        "minimum_elapsed_minutes": 0,
    },
    "two_bar_price_confirmation": {
        "mode": "two_bar_price",
        "confirmation_minutes": 15,
        "minimum_elapsed_minutes": 10,
    },
    "two_bar_price_oi_stable": {
        "mode": "two_bar_price_oi_stable",
        "confirmation_minutes": 15,
        "minimum_elapsed_minutes": 10,
    },
}


def build_config(cell: str) -> Path:
    source = MODULE.config(cell)
    payload = json.loads(source.read_text(encoding="utf-8"))
    policy = CELLS[cell]
    payload["strategy"].update(
        {
            "jump_confirmation_minutes": int(policy["confirmation_minutes"]),
            "jump_confirmation_bucket_minutes": 5,
            "jump_post_state_mode": str(policy["mode"]),
            "jump_min_confirmation_elapsed_minutes": int(
                policy["minimum_elapsed_minutes"]
            ),
            "jump_oi_max_decline_fraction": 0.01,
        }
    )
    source.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return source


def download_sidecar() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2026-07-25",
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
        str(build_config(cell)),
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
    env["C57_JUMP_TAKER_FILTER_MODE"] = "source_without_taker_filter"
    env["C57_JUMP_ARBITRATION_MODE"] = "taker_conditional"
    env["C57_JUMP_SIDE_MODE"] = "both"
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
    freeze = HERE / "JUMP_DELAYED_POST_STATE_FRESH_V1_FREEZE.md"
    if not freeze.is_file():
        raise RuntimeError("delayed post-cascade fresh freeze is missing")
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
        "experiment": "candidate-57-jump-delayed-post-state-fresh-v1",
        "binary_gate": False,
        "fresh_interval_consumed": True,
        "interval": [MODULE.START.isoformat(), MODULE.END.isoformat()],
        "cells": results,
        "policy_deltas": {
            "two_bar_price_vs_immediate": delta(
                results, "immediate_control", "two_bar_price_confirmation"
            ),
            "price_oi_vs_immediate": delta(
                results, "immediate_control", "two_bar_price_oi_stable"
            ),
            "oi_increment_over_price": delta(
                results,
                "two_bar_price_confirmation",
                "two_bar_price_oi_stable",
            ),
        },
        "external_reuse": {
            "post_liquidation_price_acceptance": (
                "wait for multiple completed bars without renewed cascade extension"
            ),
            "open_interest_stabilization": (
                "target OI not more than 1% below source-boundary OI"
            ),
        },
        "unchanged": [
            "completed 4h source jump >=2 sigma",
            "prior-only volatility window 18",
            "peer-taker conditional arbitration",
            "both reversal directions",
            "whole-event 240-minute source clock",
            "transient 0.4R arm and 1.0R escape management",
            "current-NAV 3% planned-loss sizing",
            "realistic project costs and NautilusTrader one-slot account",
        ],
    }
    MODULE.dump(MODULE.EVIDENCE / "comparison.json", comparison)

    lines = [
        "# Delayed post-cascade jump state — fresh result",
        "",
        "The delayed cells wait at least two completed five-minute bars. The "
        "OI cell additionally requires target open interest stabilization.",
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
