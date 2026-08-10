#!/usr/bin/env python3
"""Conditionally run an untouched confirm-then-select jump comparison."""
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
SOURCE = HERE / "jump_confirm_then_select_development_v1.py"
SPEC = importlib.util.spec_from_file_location(
    "candidate57_jump_confirm_then_select_fresh_base", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable candidate-pool campaign: {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

DEV_RESULT = (
    HERE
    / "evidence"
    / "jump-confirm-then-select-development-v1"
    / "comparison.json"
)
WORK = ROOT / ".work" / "candidate-57-jump-confirm-then-select-fresh-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-jump-confirm-then-select-fresh-v1"
EVIDENCE = HERE / "evidence" / "jump-confirm-then-select-fresh-v1"
CACHE = ROOT / ".cache" / "candidate-57-jump-confirm-then-select-fresh-v1"
METRICS = WORK / "binance_metrics_2025-02-27_2025-03-16.json"
START = date(2025, 3, 3)
END = date(2025, 3, 16)


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


def pf_ok(metrics: dict[str, Any]) -> bool:
    value = metrics.get("profit_factor")
    if value is None:
        return int(metrics.get("wins") or 0) > 0 and int(metrics.get("losses") or 0) == 0
    return number(value) > 1.0


def promoted() -> tuple[bool, dict[str, Any]]:
    if not DEV_RESULT.is_file():
        return False, {"reason": "DEVELOPMENT_RESULT_MISSING"}
    payload = json.loads(DEV_RESULT.read_text(encoding="utf-8"))
    cells = payload.get("cells") or {}
    control = cells.get("selected_then_confirm") or {}
    candidate = cells.get("confirm_then_select") or {}
    control_metrics = control.get("metrics") or {}
    candidate_metrics = candidate.get("metrics") or {}
    checks = {
        "control_valid": BASE.account_ok(control),
        "candidate_valid": BASE.account_ok(candidate),
        "candidate_trades_at_least_3": int(candidate_metrics.get("trades") or 0) >= 3,
        "candidate_positive_growth": number(
            candidate_metrics.get("geometric_daily_growth")
        )
        > 0.0,
        "candidate_pf_above_1": pf_ok(candidate_metrics),
        "growth_improves_control": number(
            candidate_metrics.get("geometric_daily_growth")
        )
        > number(control_metrics.get("geometric_daily_growth")),
        "return_improves_control": number(candidate_metrics.get("total_return"))
        > number(control_metrics.get("total_return")),
    }
    return all(checks.values()), {
        "checks": checks,
        "control_metrics": control_metrics,
        "candidate_metrics": candidate_metrics,
    }


def download_sidecar() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2025-02-27",
        "--end",
        END.isoformat(),
        "--output",
        str(METRICS),
        "--cache",
        str(CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def configure_base() -> None:
    BASE.WORK = WORK
    BASE.ARTIFACTS = ARTIFACTS
    BASE.EVIDENCE = EVIDENCE
    BASE.CACHE = CACHE
    BASE.METRICS = METRICS
    BASE.START = START
    BASE.END = END
    BASE.DAYS = (END - START).days + 1


def untouched_pass(results: dict[str, dict[str, Any]]) -> bool:
    control = results["selected_then_confirm"]
    candidate = results["confirm_then_select"]
    if not BASE.account_ok(control) or not BASE.account_ok(candidate):
        return False
    control_metrics = control.get("metrics") or {}
    candidate_metrics = candidate.get("metrics") or {}
    return (
        int(candidate_metrics.get("trades") or 0) >= 7
        and number(candidate_metrics.get("geometric_daily_growth")) > 0.0
        and pf_ok(candidate_metrics)
        and number(candidate_metrics.get("max_drawdown"), 1.0) <= 0.20
        and number(candidate_metrics.get("geometric_daily_growth"))
        > number(control_metrics.get("geometric_daily_growth"))
    )


def main() -> int:
    freeze = HERE / "JUMP_CONFIRM_THEN_SELECT_FRESH_V1_FREEZE.md"
    if not freeze.is_file():
        raise RuntimeError("conditional untouched freeze is missing")
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    should_run, promotion = promoted()
    if not should_run:
        result = {
            "experiment": "candidate-57-jump-confirm-then-select-fresh-v1",
            "untouched_consumed": False,
            "promotion": promotion,
            "decision": "NOT_PROMOTED_FROM_DEVELOPMENT",
        }
        dump(EVIDENCE / "comparison.json", result)
        (EVIDENCE / "RESULT.md").write_text(
            "# Confirm-then-select untouched test\n\n"
            "Untouched data was not consumed because the predeclared development "
            "promotion rule was not satisfied.\n",
            encoding="utf-8",
        )
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0

    configure_base()
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    data_status = download_sidecar()
    process_status = data_status
    results: dict[str, dict[str, Any]] = {}
    for cell in BASE.CELLS:
        code = BASE.run_cell(cell) if data_status == 0 else 1
        process_status = process_status or code
        results[cell] = BASE.load_case(cell, code)

    decision = untouched_pass(results)
    comparison = {
        "experiment": "candidate-57-jump-confirm-then-select-fresh-v1",
        "untouched_consumed": True,
        "interval": [START.isoformat(), END.isoformat()],
        "promotion": promotion,
        "cells": results,
        "confirm_then_select_delta": BASE.delta(
            results["selected_then_confirm"], results["confirm_then_select"]
        ),
        "untouched_pass": decision,
    }
    dump(EVIDENCE / "comparison.json", comparison)
    lines = [
        "# Confirm-then-select — untouched result",
        "",
        "| cell | trades | W/L | PF | geo/day | total return | MDD | candidates | confirmations | selected symbols |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for cell, row in results.items():
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
                    str(
                        diagnostics.get("jump_pool_candidates_started")
                        or diagnostics.get("jump_confirmation_pending_started")
                    ),
                    str(
                        diagnostics.get("jump_pool_candidate_confirmations")
                        or diagnostics.get("jump_confirmation_confirmed")
                    ),
                    json.dumps(diagnostics.get("selected_symbols"), sort_keys=True),
                ]
            )
            + " |"
        )
    lines.extend(["", f"Untouched pass: **{decision}**"])
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False))
    if process_status != 0 or any(
        not BASE.account_ok(row) for row in results.values()
    ):
        return int(process_status or 2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
