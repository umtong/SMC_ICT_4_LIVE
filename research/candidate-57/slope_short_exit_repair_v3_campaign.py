#!/usr/bin/env python3
"""Focused structural test of repaired Slope-is-Dope short exits."""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from trade_ledger_forensics import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
SOURCE = HERE / "slope_is_dope_1h_source_campaign.py"
SPEC = importlib.util.spec_from_file_location("candidate57_slope_v3_base", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable slope campaign: {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

WORK = ROOT / ".work" / "candidate-57-slope-short-exit-repair-v3"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-slope-short-exit-repair-v3"
EVIDENCE = HERE / "evidence" / "slope-short-exit-repair-v3"
CACHE = ROOT / ".cache" / "candidate-57-slope-short-exit-repair-v3"
FREEZE = HERE / "SLOPE_SHORT_EXIT_REPAIR_V3_FREEZE.md"
CONTROL_ROOT = HERE / "evidence" / "slope-is-dope-1h-roi-fix-v2" / "cases"

CELLS: dict[str, tuple[str, str, str]] = {
    "symmetric_short": ("claim", "short", "symmetric_high"),
    "symmetric_both": ("claim", "both", "symmetric_high"),
    "ma_only_short": ("claim", "short", "ma_only"),
    "ma_only_both": ("claim", "both", "ma_only"),
}
FEATURE_KEYS = (
    "adx",
    "rsi",
    "fast_slope",
    "slow_slope",
    "source_score",
    "source_stop_fraction",
)


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


def build_config(cell: str) -> Path:
    _, side, mode = CELLS[cell]
    original_variants = BASE.VARIANTS
    original_work = BASE.WORK
    try:
        BASE.VARIANTS = {cell: ("claim", side)}
        BASE.WORK = WORK
        path = BASE.build_config(cell)
    finally:
        BASE.VARIANTS = original_variants
        BASE.WORK = original_work
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["strategy"]["slope_short_exit_mode"] = mode
    dump(path, payload)
    return path


def run_case(stage: Any, cell: str) -> dict[str, Any]:
    output = ARTIFACTS / stage.name / cell
    workspace = WORK / "workspace" / stage.name / cell
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(C51 / "launch.py"),
            "--config",
            str(build_config(cell)),
            "--start",
            stage.start.isoformat(),
            "--end",
            stage.end.isoformat(),
            "--cache",
            str(CACHE),
            "--output",
            str(output),
            "--workspace",
            str(workspace),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(C51)},
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    _, side, mode = CELLS[cell]
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "stage": stage.name,
            "cell": cell,
            "side": side,
            "short_exit_mode": mode,
            "produced": False,
            "returncode": completed.returncode,
        }
        dump(EVIDENCE / "cases" / f"{stage.name}-{cell}.json", row)
        return row
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    expected = int(metrics.get("trades") or 0)
    row = {
        "stage": stage.name,
        "cell": cell,
        "side": side,
        "short_exit_mode": mode,
        "produced": True,
        "returncode": 0,
        "metrics": {
            key: metrics.get(key)
            for key in (
                "starting_nav",
                "ending_nav",
                "total_return",
                "geometric_daily_growth",
                "max_drawdown",
                "min_equity",
                "trades",
                "wins",
                "losses",
                "win_rate",
                "profit_factor",
                "expectancy_usdt",
                "active_days",
                "open_position_rows_at_end",
                "active_order_rows_at_end",
                "gate_checks",
            )
        },
        "diagnostics": {
            key: diagnostics.get(key)
            for key in (
                "source_signals_before_execution_filters",
                "entry_submissions",
                "selected_symbols",
                "slope_source_signal_exits",
                "slope_positive_trailing_exits",
                "slope_default_trailing_exits",
                "slope_roi_exits",
                "slope_trailing_activations",
                "max_open_positions_observed",
                "max_simultaneous_entry_intents",
                "global_position_violations",
                "order_rejections",
            )
        },
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / f"{stage.name}-{cell}.json", row)
    return row


def load_control(side: str) -> dict[str, Any]:
    name = "claim_level_short" if side == "short" else "claim_level_both"
    path = CONTROL_ROOT / f"development-{name}.json"
    if not path.is_file():
        raise RuntimeError(f"missing preexisting literal control: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def account_ok(row: dict[str, Any]) -> bool:
    if not row.get("produced"):
        return False
    metrics = row.get("metrics") or {}
    diagnostics = row.get("diagnostics") or {}
    checks = metrics.get("gate_checks") or {}
    ledger = row.get("trade_forensics") or {}
    return (
        int(diagnostics.get("global_position_violations") or 0) == 0
        and int(diagnostics.get("order_rejections") or 0) == 0
        and int(diagnostics.get("max_open_positions_observed") or 0) <= 1
        and int(diagnostics.get("max_simultaneous_entry_intents") or 0) <= 1
        and int(metrics.get("open_position_rows_at_end") or 0) == 0
        and int(metrics.get("active_order_rows_at_end") or 0) == 0
        and bool(checks.get("no_liquidation", True))
        and bool(ledger.get("ledger_matches_metrics"))
    )


def exit_share(row: dict[str, Any]) -> float:
    metrics = row.get("metrics") or {}
    diagnostics = row.get("diagnostics") or {}
    trades = int(metrics.get("trades") or 0)
    return (
        int(diagnostics.get("slope_source_signal_exits") or 0) / trades
        if trades
        else 0.0
    )


def positive(row: dict[str, Any], days: int) -> bool:
    metrics = row.get("metrics") or {}
    pf = metrics.get("profit_factor")
    pf_ok = (
        number(pf) > 1.0
        if pf is not None
        else int(metrics.get("wins") or 0) > 0 and int(metrics.get("losses") or 0) == 0
    )
    return (
        account_ok(row)
        and int(metrics.get("trades") or 0) >= days
        and number(metrics.get("geometric_daily_growth")) > 0.0
        and number(metrics.get("expectancy_usdt")) > 0.0
        and pf_ok
        and number(metrics.get("max_drawdown"), 1.0) <= 0.20
    )


def development_value(row: dict[str, Any]) -> dict[str, Any]:
    side = str(row.get("side"))
    control = load_control(side)
    control_metrics = control.get("metrics") or {}
    metrics = row.get("metrics") or {}
    control_trades = int(control_metrics.get("trades") or 0)
    control_diag = control.get("diagnostics") or {}
    control_exit_share = (
        int(control_diag.get("slope_source_signal_exits") or 0) / control_trades
        if control_trades
        else 0.0
    )
    delta_return = number(metrics.get("total_return")) - number(
        control_metrics.get("total_return")
    )
    reduced_share = control_exit_share - exit_share(row)
    return {
        "control_variant": (
            "claim_level_short" if side == "short" else "claim_level_both"
        ),
        "control_total_return": control_metrics.get("total_return"),
        "candidate_total_return": metrics.get("total_return"),
        "delta_total_return": delta_return,
        "control_source_exit_share": control_exit_share,
        "candidate_source_exit_share": exit_share(row),
        "source_exit_share_reduction": reduced_share,
        "positive_development": positive(row, BASE.DEVELOPMENT.days),
        "structurally_improved": (
            account_ok(row)
            and int(metrics.get("trades") or 0) >= 7
            and delta_return >= 0.01
            and reduced_share >= 0.30
        ),
    }


def rank(row: dict[str, Any]) -> tuple[float, float, int, str]:
    metrics = row.get("metrics") or {}
    value = row.get("development_value") or {}
    return (
        -number(metrics.get("geometric_daily_growth"), -math.inf),
        -number(value.get("delta_total_return"), -math.inf),
        -int(metrics.get("trades") or 0),
        str(row.get("cell")),
    )


def render(comparison: dict[str, Any]) -> None:
    lines = [
        "# Slope-is-Dope short-exit repair v3",
        "",
        "Literal controls are preexisting v2 accounts. New rows preserve every trade in the case JSON files.",
        "",
    ]
    for stage_name in ("development", "untouched", "continuous_30d"):
        lines += [
            f"## {stage_name}",
            "",
            "| cell | trades | W/L | PF | geo/day | return | MDD | source-exit share |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for cell, row in comparison[stage_name].items():
            metrics = row.get("metrics") or {}
            lines.append(
                f"| {cell} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
                f"{metrics.get('profit_factor')} | {metrics.get('geometric_daily_growth')} | "
                f"{metrics.get('total_return')} | {metrics.get('max_drawdown')} | {exit_share(row)} |"
            )
        lines.append("")
    lines += [
        "## Allocation",
        "",
        f"- promoted to untouched: {comparison['development_survivors']}",
        f"- positive untouched survivors: {comparison['untouched_positive_survivors']}",
        f"- continuous winner: {comparison['continuous_winner']}",
        f"- strict project pass: {comparison['strict_project_pass']}",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("frozen Slope short-exit repair specification missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = {cell: run_case(BASE.DEVELOPMENT, cell) for cell in CELLS}
    for row in development.values():
        row["development_value"] = development_value(row)
        dump(EVIDENCE / "cases" / f"development-{row['cell']}.json", row)
    eligible = [
        row
        for row in development.values()
        if (row["development_value"]["positive_development"]
            or row["development_value"]["structurally_improved"])
    ]
    eligible.sort(key=rank)
    survivors = [str(row["cell"]) for row in eligible[:2]]
    untouched = {cell: run_case(BASE.UNTOUCHED, cell) for cell in survivors}
    positive_names = [
        cell
        for cell, row in untouched.items()
        if positive(row, BASE.UNTOUCHED.days)
    ]
    positive_names.sort(key=lambda cell: rank(untouched[cell]))
    winner = positive_names[0] if positive_names else None
    continuous = {winner: run_case(BASE.CONTINUOUS, winner)} if winner else {}
    strict_pass = bool(
        winner
        and positive(continuous[winner], BASE.CONTINUOUS.days)
        and number((continuous[winner].get("metrics") or {}).get("geometric_daily_growth")) >= 0.01
    )

    comparison = {
        "experiment": "candidate-57-slope-short-exit-repair-v3",
        "binary_gate": False,
        "structural_failure": "literal short close > prior rolling minimum exits almost continuously",
        "literal_controls": {
            "short": load_control("short"),
            "both": load_control("both"),
        },
        "cells": CELLS,
        "development": development,
        "development_survivors": survivors,
        "untouched": untouched,
        "untouched_positive_survivors": positive_names,
        "continuous_30d": continuous,
        "continuous_winner": winner,
        "strict_project_pass": strict_pass,
    }
    dump(EVIDENCE / "comparison.json", comparison)
    render(comparison)
    print(
        json.dumps(
            {
                "development_survivors": survivors,
                "untouched_positive_survivors": positive_names,
                "continuous_winner": winner,
                "strict_project_pass": strict_pass,
            },
            indent=2,
            sort_keys=True,
        )
    )

    rows = list(development.values()) + list(untouched.values()) + list(continuous.values())
    if any(not row.get("produced") for row in rows):
        return 1
    if any(not account_ok(row) for row in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
