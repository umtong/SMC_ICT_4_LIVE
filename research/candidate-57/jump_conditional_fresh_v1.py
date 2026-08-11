#!/usr/bin/env python3
"""Fresh account comparison for the frozen 4h jump state/arbitration policies."""
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
    "candidate57_jump_conditional_fresh_base", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable jump campaign: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MODULE.WORK = ROOT / ".work" / "candidate-57-jump-conditional-fresh-v1"
MODULE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-jump-conditional-fresh-v1"
MODULE.EVIDENCE = HERE / "evidence" / "jump-conditional-fresh-v1"
MODULE.CACHE = ROOT / ".cache" / "candidate-57-jump-conditional-fresh-v1"
MODULE.METRICS = MODULE.WORK / "binance_metrics_2024-08-29_2024-09-15.json"
MODULE.START = date(2024, 9, 2)
MODULE.END = date(2024, 9, 15)
MODULE.DAYS = (MODULE.END - MODULE.START).days + 1

CELLS: dict[str, tuple[str, str]] = {
    "source_max_z": ("source_without_taker_filter", "source_max_z"),
    "least_qualifying_z": (
        "source_without_taker_filter",
        "least_qualifying_z",
    ),
    "taker_conditional": (
        "source_without_taker_filter",
        "taker_conditional",
    ),
    "least_z_taker_3of4": (
        "peer_taker_alignment_3of4",
        "least_qualifying_z",
    ),
}


def download_sidecar() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2024-08-29",
        "--end",
        MODULE.END.isoformat(),
        "--output",
        str(MODULE.METRICS),
        "--cache",
        str(MODULE.CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def run_cell(cell: str) -> int:
    filter_mode, arbitration_mode = CELLS[cell]
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
    env["C57_JUMP_TAKER_FILTER_MODE"] = filter_mode
    env["C57_JUMP_ARBITRATION_MODE"] = arbitration_mode
    env["C57_JUMP_SIDE_MODE"] = "both"
    env["C57_JUMP_TAKER_METRICS_PATH"] = str(MODULE.METRICS)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def actual_account(result: dict[str, Any]) -> dict[str, Any]:
    return result.get("actual_account") or {}


def compact(result: dict[str, Any]) -> dict[str, Any]:
    account = actual_account(result)
    return {
        "produced": result.get("produced"),
        "completed_trades": result.get("actual_completed_trades"),
        "source_independent_boundaries": result.get(
            "source_independent_boundaries"
        ),
        "accepted_independent_boundaries": result.get(
            "accepted_independent_boundaries"
        ),
        "collision_boundaries": (result.get("collision_boundaries") or {}).get(
            "count"
        ),
        "wins": account.get("wins"),
        "losses": account.get("losses"),
        "profit_factor": account.get("profit_factor"),
        "geometric_daily_growth": account.get("geometric_daily_growth"),
        "total_return": account.get("total_return"),
        "max_drawdown": account.get("max_drawdown"),
        "ending_nav": account.get("ending_nav"),
        "end_validity": result.get("end_validity"),
    }


def delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    left_account = actual_account(left)
    right_account = actual_account(right)
    keys = (
        "ending_nav",
        "geometric_daily_growth",
        "total_return",
        "max_drawdown",
        "profit_factor",
        "trades",
        "wins",
        "losses",
    )
    return {
        key: MODULE.number(left_account.get(key), 0.0)
        - MODULE.number(right_account.get(key), 0.0)
        for key in keys
    }


def validity_ok(result: dict[str, Any]) -> bool:
    if not result.get("produced"):
        return False
    validity = result.get("end_validity") or {}
    return (
        validity.get("no_open_positions_at_end") is not False
        and validity.get("no_active_orders_at_end") is not False
        and validity.get("no_global_position_violation") is not False
        and validity.get("no_order_rejections") is not False
        and validity.get("no_liquidation") is not False
    )


def target(account: dict[str, Any]) -> bool:
    return (
        int(account.get("trades") or 0) >= MODULE.DAYS
        and MODULE.number(account.get("geometric_daily_growth")) >= 0.01
        and MODULE.number(account.get("profit_factor")) > 1.0
        and MODULE.number(account.get("ending_nav")) > MODULE.number(
            account.get("starting_nav")
        )
    )


def render(comparison: dict[str, Any]) -> None:
    lines = [
        "# Fresh 4h jump state/arbitration account comparison",
        "",
        f"Evaluated entries: `{MODULE.START.isoformat()}` through `{MODULE.END.isoformat()}` UTC. Simultaneous symbols at one completed 4h boundary are one causal opportunity.",
        "",
        "| cell | source boundaries | accepted | trades | W/L | PF | geo/day | return | MDD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in CELLS:
        result = comparison["cells"][cell]
        account = actual_account(result)
        lines.append(
            f"| {cell} | {result.get('source_independent_boundaries')} | "
            f"{result.get('accepted_independent_boundaries')} | "
            f"{result.get('actual_completed_trades')} | "
            f"{account.get('wins')}/{account.get('losses')} | "
            f"{account.get('profit_factor')} | "
            f"{account.get('geometric_daily_growth')} | "
            f"{account.get('total_return')} | {account.get('max_drawdown')} |"
        )
    lines += [
        "",
        "## Frozen contrasts",
        "",
        f"- conditional minus source max-z: `{json.dumps(comparison['conditional_minus_source'], sort_keys=True)}`",
        f"- conditional minus least-z: `{json.dumps(comparison['conditional_minus_least'], sort_keys=True)}`",
        f"- filtered least-z minus conditional: `{json.dumps(comparison['filtered_minus_conditional'], sort_keys=True)}`",
        f"- all account paths valid: `{comparison['all_accounts_valid']}`",
        f"- conditional strict project target: `{comparison['conditional_strict_project_target']}`",
        "",
        "A favorable row is not promoted without reading collision choices, rejected boundaries, slot occupancy and outlier concentration in the retained case evidence.",
    ]
    (MODULE.EVIDENCE / "RESULT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    freeze = HERE / "JUMP_CONDITIONAL_FRESH_V1_FREEZE.md"
    if not freeze.is_file():
        raise RuntimeError("frozen fresh jump comparison missing")
    for path in (MODULE.WORK, MODULE.ARTIFACTS, MODULE.CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if MODULE.EVIDENCE.exists():
        shutil.rmtree(MODULE.EVIDENCE)
    MODULE.EVIDENCE.mkdir(parents=True, exist_ok=True)

    data_status = download_sidecar()
    process_status = data_status
    results: dict[str, Any] = {}
    for cell in CELLS:
        code = run_cell(cell) if data_status == 0 else 1
        process_status = process_status or code
        results[cell] = MODULE.analyze_cell(cell, code)

    comparison = {
        "experiment": "candidate-57-jump-conditional-fresh-v1",
        "policy_frozen_before_interval": True,
        "interval": [MODULE.START.isoformat(), MODULE.END.isoformat()],
        "days": MODULE.DAYS,
        "metrics_sidecar": {
            "source": "Binance Vision futures/um daily metrics",
            "strict_asof_max_age_minutes": 10,
            "all_four_peers_required_for_conditional": True,
            "bytes": MODULE.METRICS.stat().st_size
            if MODULE.METRICS.is_file()
            else None,
        },
        "cells": results,
        "compact": {cell: compact(result) for cell, result in results.items()},
        "conditional_minus_source": delta(
            results["taker_conditional"], results["source_max_z"]
        ),
        "conditional_minus_least": delta(
            results["taker_conditional"], results["least_qualifying_z"]
        ),
        "filtered_minus_conditional": delta(
            results["least_z_taker_3of4"], results["taker_conditional"]
        ),
        "all_accounts_valid": all(validity_ok(result) for result in results.values()),
        "conditional_strict_project_target": target(
            actual_account(results["taker_conditional"])
        ),
    }
    MODULE.dump(MODULE.EVIDENCE / "comparison.json", comparison)
    render(comparison)
    print(json.dumps(comparison["compact"], indent=2, sort_keys=True, allow_nan=False))

    if process_status != 0:
        return process_status
    if any(not result.get("produced") for result in results.values()):
        return 1
    if not comparison["all_accounts_valid"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
