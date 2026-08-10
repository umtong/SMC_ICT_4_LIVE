#!/usr/bin/env python3
"""Development account replay for peer-taker conditional jump arbitration."""
from __future__ import annotations

from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE = HERE / "jump_taker_alignment_fresh_campaign.py"
SPEC = importlib.util.spec_from_file_location(
    "candidate57_jump_taker_conditional_development_base", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable jump campaign: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MODULE.WORK = ROOT / ".work" / "candidate-57-jump-taker-conditional-development-v1"
MODULE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-jump-taker-conditional-development-v1"
MODULE.EVIDENCE = HERE / "evidence" / "jump-taker-conditional-development-v1"
MODULE.CACHE = ROOT / ".cache" / "candidate-57-jump-taker-conditional-development-v1"
MODULE.METRICS = MODULE.WORK / "binance_metrics_2026-06-11_2026-06-28.json"
MODULE.START = date(2026, 6, 15)
MODULE.END = date(2026, 6, 28)
MODULE.DAYS = (MODULE.END - MODULE.START).days + 1
CELL = "taker_conditional"


def download_sidecar() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2026-06-11",
        "--end",
        MODULE.END.isoformat(),
        "--output",
        str(MODULE.METRICS),
        "--cache",
        str(MODULE.CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def run_cell() -> int:
    output = MODULE.ARTIFACTS / CELL
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(MODULE.C51 / "launch.py"),
        "--config",
        str(MODULE.config(CELL)),
        "--start",
        MODULE.START.isoformat(),
        "--end",
        MODULE.END.isoformat(),
        "--cache",
        str(MODULE.CACHE / "bars"),
        "--output",
        str(output),
        "--workspace",
        str(MODULE.WORK / CELL / "workspace"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(MODULE.C51)
    env["C57_JUMP_TAKER_FILTER_MODE"] = "source_without_taker_filter"
    env["C57_JUMP_ARBITRATION_MODE"] = "taker_conditional"
    env["C57_JUMP_TAKER_METRICS_PATH"] = str(MODULE.METRICS)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def main() -> int:
    for path in (MODULE.WORK, MODULE.ARTIFACTS, MODULE.CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if MODULE.EVIDENCE.exists():
        shutil.rmtree(MODULE.EVIDENCE)
    MODULE.EVIDENCE.mkdir(parents=True, exist_ok=True)

    data_status = download_sidecar()
    run_status = run_cell() if data_status == 0 else 1
    result = MODULE.analyze_cell(CELL, run_status)

    controls_path = (
        HERE
        / "evidence"
        / "jump-state-arbitration-fresh-v1"
        / "comparison.json"
    )
    controls = None
    if controls_path.is_file():
        payload = json.loads(controls_path.read_text(encoding="utf-8"))
        controls = {
            name: {
                "actual_account": (payload.get("cells") or {}).get(name, {}).get(
                    "actual_account"
                ),
                "actual_completed_trades": (payload.get("cells") or {}).get(
                    name, {}
                ).get("actual_completed_trades"),
            }
            for name in (
                "source_max_z__no_taker",
                "least_z__no_taker",
                "source_max_z__taker_3of4",
                "least_z__taker_3of4",
            )
        }
    comparison = {
        "experiment": "candidate-57-jump-taker-conditional-development-v1",
        "development_only": True,
        "source_interval": [MODULE.START.isoformat(), MODULE.END.isoformat()],
        "policy": {
            "aligned_3of4_or_4of4": "source maximum absolute z",
            "otherwise": "least absolute already-qualified z",
            "entry_filter": "none beyond source event",
            "state_join": "strict as-of Binance taker ratio, all four peers, max age 10m",
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
        "conditional_account": result,
        "same_interval_factorial_controls": controls,
    }
    MODULE.dump(MODULE.EVIDENCE / "comparison.json", comparison)
    account = result.get("actual_account") or {}
    lines = [
        "# Peer-taker conditional arbitration — development account replay",
        "",
        "When at least three of four peer taker ratios align with the proposed "
        "reversal, the router uses source max-z; otherwise it uses least-z.",
        "",
        "| trades | W/L | PF | geo/day | total return | MDD | accepted boundaries |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        "| "
        + " | ".join(
            [
                str(result.get("actual_completed_trades")),
                f"{account.get('wins')}/{account.get('losses')}",
                str(account.get("profit_factor")),
                str(account.get("geometric_daily_growth")),
                str(account.get("total_return")),
                str(account.get("max_drawdown")),
                str(result.get("accepted_independent_boundaries")),
            ]
        )
        + " |",
    ]
    (MODULE.EVIDENCE / "RESULT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False))
    if data_status != 0:
        return data_status
    if run_status != 0 or not result.get("produced"):
        return run_status or 1
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
