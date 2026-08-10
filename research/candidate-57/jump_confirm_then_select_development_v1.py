#!/usr/bin/env python3
"""Development comparison of source-boundary vs post-confirmation arbitration."""
from __future__ import annotations

from datetime import date
import importlib.util
import json
import math
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
    "candidate57_jump_confirm_pool_development_base", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable jump campaign: {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

WORK = ROOT / ".work" / "candidate-57-jump-confirm-then-select-development-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-jump-confirm-then-select-development-v1"
EVIDENCE = HERE / "evidence" / "jump-confirm-then-select-development-v1"
CACHE = ROOT / ".cache" / "candidate-57-jump-confirm-then-select-development-v1"
METRICS = WORK / "binance_metrics_2026-07-25_2026-08-09.json"
START = date(2026, 7, 29)
END = date(2026, 8, 9)
DAYS = (END - START).days + 1

CELLS = {
    "selected_then_confirm": "selected_then_confirm",
    "confirm_then_select": "confirm_then_select",
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def download_sidecar() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2026-07-25",
        "--end",
        END.isoformat(),
        "--output",
        str(METRICS),
        "--cache",
        str(CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def build_config(cell: str) -> Path:
    BASE.WORK = WORK
    path = BASE.config(cell)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["strategy"].update(
        {
            "jump_confirmation_minutes": 15,
            "jump_confirmation_bucket_minutes": 5,
            "jump_post_state_mode": "two_bar_price",
            "jump_min_confirmation_elapsed_minutes": 10,
            "jump_oi_max_decline_fraction": 0.01,
            "jump_confirmation_pool_mode": CELLS[cell],
        }
    )
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def run_cell(cell: str) -> int:
    output = ARTIFACTS / cell
    workspace = WORK / cell / "workspace"
    if output.exists():
        shutil.rmtree(output)
    if workspace.exists():
        shutil.rmtree(workspace)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(BASE.C51 / "launch.py"),
        "--config",
        str(build_config(cell)),
        "--start",
        START.isoformat(),
        "--end",
        END.isoformat(),
        "--cache",
        str(CACHE / "bars"),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BASE.C51)
    env["C57_JUMP_TAKER_FILTER_MODE"] = "source_without_taker_filter"
    env["C57_JUMP_ARBITRATION_MODE"] = "taker_conditional"
    env["C57_JUMP_SIDE_MODE"] = "both"
    env["C57_JUMP_TAKER_METRICS_PATH"] = str(METRICS)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def load_case(cell: str, returncode: int) -> dict[str, Any]:
    output = ARTIFACTS / cell
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if (
        returncode != 0
        or not metrics_path.is_file()
        or not diagnostics_path.is_file()
    ):
        return {"cell": cell, "produced": False, "returncode": returncode}
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    metric_keys = (
        "starting_nav",
        "ending_nav",
        "total_return",
        "geometric_daily_growth",
        "max_drawdown",
        "trades",
        "wins",
        "losses",
        "win_rate",
        "profit_factor",
        "expectancy_usdt",
        "expectancy_r",
        "average_hold_minutes",
        "largest_winner_share",
        "largest_loser_share",
    )
    diagnostic_keys = (
        "source_signals_before_execution_filters",
        "entry_submissions",
        "entry_expirations",
        "selected_symbols",
        "route_counts",
        "unresolved_reason_counts",
        "jump_confirmation_pending_started",
        "jump_confirmation_confirmed",
        "jump_confirmation_expired",
        "jump_pool_source_events",
        "jump_pool_candidates_started",
        "jump_pool_candidate_confirmations",
        "jump_pool_multi_confirmation_boundaries",
        "jump_pool_expired_candidates",
        "jump_pool_selected_entries",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
    )
    return {
        "cell": cell,
        "produced": True,
        "returncode": returncode,
        "metrics": {key: metrics.get(key) for key in metric_keys},
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
    }


def account_ok(row: dict[str, Any]) -> bool:
    if not row.get("produced"):
        return False
    diagnostics = row.get("diagnostics") or {}
    return (
        int(diagnostics.get("global_position_violations") or 0) == 0
        and int(diagnostics.get("order_rejections") or 0) == 0
        and int(diagnostics.get("max_open_positions_observed") or 0) <= 1
        and int(diagnostics.get("max_simultaneous_entry_intents") or 0) <= 1
    )


def delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_metrics = left.get("metrics") or {}
    right_metrics = right.get("metrics") or {}
    return {
        "delta_trades": int(right_metrics.get("trades") or 0)
        - int(left_metrics.get("trades") or 0),
        "delta_geometric_daily_growth": number(
            right_metrics.get("geometric_daily_growth")
        )
        - number(left_metrics.get("geometric_daily_growth")),
        "delta_total_return": number(right_metrics.get("total_return"))
        - number(left_metrics.get("total_return")),
        "delta_max_drawdown": number(right_metrics.get("max_drawdown"))
        - number(left_metrics.get("max_drawdown")),
        "delta_profit_factor": number(right_metrics.get("profit_factor"))
        - number(left_metrics.get("profit_factor")),
    }


def main() -> int:
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    data_status = download_sidecar()
    process_status = data_status
    results: dict[str, dict[str, Any]] = {}
    for cell in CELLS:
        code = run_cell(cell) if data_status == 0 else 1
        process_status = process_status or code
        results[cell] = load_case(cell, code)

    comparison = {
        "experiment": "candidate-57-jump-confirm-then-select-development-v1",
        "development_only": True,
        "source_interval": [START.isoformat(), END.isoformat()],
        "consumed_control_result_preexisting": True,
        "policy": {
            "selected_then_confirm": (
                "source-boundary arbitration chooses one symbol, then waits for its two-bar confirmation"
            ),
            "confirm_then_select": (
                "all simultaneous qualified symbols remain non-order candidates; first confirmation boundary is arbitrated causally"
            ),
        },
        "cells": results,
        "confirm_then_select_delta": delta(
            results["selected_then_confirm"], results["confirm_then_select"]
        ),
        "unchanged": [
            "completed 4h source jump >=2 sigma",
            "prior-only volatility window 18",
            "peer-taker conditional scoring",
            "two completed 5m bars and terminal-extreme confirmation",
            "15-minute confirmation expiry",
            "whole-event 240-minute source clock",
            "transient 0.4R arm and 1.0R escape management",
            "current-NAV 3% planned-loss sizing",
            "realistic project costs and NautilusTrader one-slot account",
        ],
    }
    dump(EVIDENCE / "comparison.json", comparison)

    lines = [
        "# Jump arbitration after confirmation — development comparison",
        "",
        "Candidate-pool entries are not orders. Only the selected confirmed "
        "candidate is submitted to the one-slot account.",
        "",
        "| cell | trades | W/L | PF | geo/day | total return | MDD | avg hold | source events | candidates | confirmations | selected symbols |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for cell in CELLS:
        row = results[cell]
        metrics = row.get("metrics") or {}
        diagnostics = row.get("diagnostics") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    cell,
                    str(metrics.get("trades")),
                    f"{metrics.get('wins')}/{metrics.get('losses')}",
                    str(metrics.get("profit_factor")),
                    str(metrics.get("geometric_daily_growth")),
                    str(metrics.get("total_return")),
                    str(metrics.get("max_drawdown")),
                    str(metrics.get("average_hold_minutes")),
                    str(
                        diagnostics.get("jump_pool_source_events")
                        or diagnostics.get("jump_confirmation_pending_started")
                    ),
                    str(
                        diagnostics.get("jump_pool_candidates_started")
                        or diagnostics.get("jump_confirmation_pending_started")
                    ),
                    str(
                        diagnostics.get("jump_pool_candidate_confirmations")
                        or diagnostics.get("jump_confirmation_confirmed")
                    ),
                    json.dumps(
                        diagnostics.get("selected_symbols"), sort_keys=True
                    ),
                ]
            )
            + " |"
        )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False))

    if process_status != 0 or any(not account_ok(row) for row in results.values()):
        return int(process_status or 2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
