#!/usr/bin/env python3
"""Multi-regime replication of the frozen two-bar jump confirmation rule."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
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
    "candidate57_jump_delayed_replication_base", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable jump campaign: {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

WORK_ROOT = ROOT / ".work" / "candidate-57-jump-delayed-price-replication-v1"
ARTIFACT_ROOT = ROOT / "artifacts" / "candidate-57-jump-delayed-price-replication-v1"
EVIDENCE = HERE / "evidence" / "jump-delayed-price-replication-v1"
CACHE_ROOT = ROOT / ".cache" / "candidate-57-jump-delayed-price-replication-v1"
FREEZE = HERE / "JUMP_DELAYED_PRICE_REPLICATION_V1_FREEZE.md"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def metrics_start(self) -> date:
        return self.start - timedelta(days=4)


STAGES = (
    Stage("february_2025", date(2025, 2, 3), date(2025, 2, 16)),
    Stage("july_2025", date(2025, 7, 7), date(2025, 7, 20)),
    Stage("january_2026", date(2026, 1, 12), date(2026, 1, 25)),
)

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
        return float(value)
    except (TypeError, ValueError):
        return default


def configure_base(stage: Stage) -> Path:
    work = WORK_ROOT / stage.name
    artifacts = ARTIFACT_ROOT / stage.name
    evidence = EVIDENCE / stage.name
    cache = CACHE_ROOT / stage.name
    metrics = work / (
        f"binance_metrics_{stage.metrics_start.isoformat()}_{stage.end.isoformat()}.json"
    )
    BASE.WORK = work
    BASE.ARTIFACTS = artifacts
    BASE.EVIDENCE = evidence
    BASE.CACHE = cache
    BASE.METRICS = metrics
    BASE.START = stage.start
    BASE.END = stage.end
    BASE.DAYS = stage.days
    for path in (work, artifacts, evidence, cache):
        path.mkdir(parents=True, exist_ok=True)
    return metrics


def download_sidecar(stage: Stage, metrics: Path) -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        stage.metrics_start.isoformat(),
        "--end",
        stage.end.isoformat(),
        "--output",
        str(metrics),
        "--cache",
        str(BASE.CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def build_config(cell: str) -> Path:
    path = BASE.config(cell)
    payload = json.loads(path.read_text(encoding="utf-8"))
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
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def run_cell(cell: str) -> int:
    output = BASE.ARTIFACTS / cell
    workspace = BASE.WORK / cell / "workspace"
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
        BASE.START.isoformat(),
        "--end",
        BASE.END.isoformat(),
        "--cache",
        str(BASE.CACHE / "bars"),
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
    env["C57_JUMP_TAKER_METRICS_PATH"] = str(BASE.METRICS)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_account = left.get("actual_account") or {}
    right_account = right.get("actual_account") or {}
    return {
        "delta_trades": int(right.get("actual_completed_trades") or 0)
        - int(left.get("actual_completed_trades") or 0),
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


def cell_positive(row: dict[str, Any]) -> bool:
    account = row.get("actual_account") or {}
    return (
        row.get("produced") is True
        and int(row.get("actual_completed_trades") or 0) > 0
        and number(account.get("geometric_daily_growth")) > 0.0
        and number(account.get("profit_factor")) > 1.0
    )


def valid(row: dict[str, Any]) -> bool:
    if not row.get("produced"):
        return False
    validity = row.get("end_validity") or {}
    return not any(
        validity.get(key) is False
        for key in (
            "no_open_positions_at_end",
            "no_active_orders_at_end",
            "no_global_position_violation",
        )
    )


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("frozen delayed replication specification missing")
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    process_status = 0
    stage_results: dict[str, dict[str, Any]] = {}

    for stage in STAGES:
        metrics = configure_base(stage)
        data_status = download_sidecar(stage, metrics)
        process_status = process_status or data_status
        cells: dict[str, dict[str, Any]] = {}
        for cell in CELLS:
            code = run_cell(cell) if data_status == 0 else 1
            process_status = process_status or code
            result = BASE.analyze_cell(cell, code)
            result["declared_policy"] = CELLS[cell]
            cells[cell] = result
        stage_results[stage.name] = {
            "stage": asdict(stage) | {
                "days": stage.days,
                "metrics_start": stage.metrics_start,
            },
            "cells": cells,
            "price_confirmation_delta": delta(
                cells["immediate_control"],
                cells["two_bar_price_confirmation"],
            ),
        }

    delayed_rows = [
        stage_results[stage.name]["cells"]["two_bar_price_confirmation"]
        for stage in STAGES
    ]
    control_rows = [
        stage_results[stage.name]["cells"]["immediate_control"]
        for stage in STAGES
    ]
    aggregate = {
        "delayed_positive_accounts": sum(cell_positive(row) for row in delayed_rows),
        "control_positive_accounts": sum(cell_positive(row) for row in control_rows),
        "delayed_total_trades": sum(
            int(row.get("actual_completed_trades") or 0) for row in delayed_rows
        ),
        "control_total_trades": sum(
            int(row.get("actual_completed_trades") or 0) for row in control_rows
        ),
        "delayed_total_return_sum_descriptive_only": sum(
            number((row.get("actual_account") or {}).get("total_return"))
            for row in delayed_rows
        ),
        "control_total_return_sum_descriptive_only": sum(
            number((row.get("actual_account") or {}).get("total_return"))
            for row in control_rows
        ),
        "delayed_growth_by_stage": {
            stage.name: number(
                (
                    stage_results[stage.name]["cells"][
                        "two_bar_price_confirmation"
                    ].get("actual_account")
                    or {}
                ).get("geometric_daily_growth")
            )
            for stage in STAGES
        },
        "selected_symbols_by_stage": {
            stage.name: stage_results[stage.name]["cells"][
                "two_bar_price_confirmation"
            ].get("selected_symbols")
            for stage in STAGES
        },
        "replication_rule_pass": (
            sum(cell_positive(row) for row in delayed_rows) >= 2
            and sum(int(row.get("actual_completed_trades") or 0) for row in delayed_rows)
            >= 6
        ),
    }
    comparison = {
        "experiment": "candidate-57-jump-delayed-price-replication-v1",
        "binary_gate": False,
        "intervals_are_separate_accounts": True,
        "rule_was_frozen_before_all_three_intervals": True,
        "stages": stage_results,
        "aggregate_descriptive_only": aggregate,
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
    dump(EVIDENCE / "comparison.json", comparison)

    lines = [
        "# Two-bar jump confirmation — multi-regime replication",
        "",
        "Each interval is a separate continuous account. Returns are not "
        "concatenated or compounded across the gaps.",
        "",
        "| interval | cell | trades | W/L | PF | geo/day | total return | MDD | selected symbols |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for stage in STAGES:
        for cell in CELLS:
            row = stage_results[stage.name]["cells"][cell]
            account = row.get("actual_account") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        stage.name,
                        cell,
                        str(row.get("actual_completed_trades")),
                        f"{account.get('wins')}/{account.get('losses')}",
                        str(account.get("profit_factor")),
                        str(account.get("geometric_daily_growth")),
                        str(account.get("total_return")),
                        str(account.get("max_drawdown")),
                        json.dumps(row.get("selected_symbols"), sort_keys=True),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Replication summary",
            "",
            f"- delayed positive accounts: {aggregate['delayed_positive_accounts']} / {len(STAGES)}",
            f"- delayed completed trades: {aggregate['delayed_total_trades']}",
            f"- replication rule pass: {aggregate['replication_rule_pass']}",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, indent=2, sort_keys=True, allow_nan=False))

    all_rows = [
        stage_results[stage.name]["cells"][cell]
        for stage in STAGES
        for cell in CELLS
    ]
    if process_status != 0 or any(not valid(row) for row in all_rows):
        return int(process_status or 2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
