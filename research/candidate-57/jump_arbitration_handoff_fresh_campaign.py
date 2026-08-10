#!/usr/bin/env python3
"""Run the frozen 2x2 jump arbitration × boundary-handoff experiment.

The campaign changes only the two factors frozen in
``JUMP_ARBITRATION_HANDOFF_FRESH_V1_FREEZE.md``.  It preserves full per-cell
Nautilus evidence and reports account-path differences without turning the
result into a binary gate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import csv
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
C51 = REPO / "research" / "candidate-51"
WORK_ROOT = REPO / ".work" / "candidate-57-jump-arbitration-handoff-fresh-v1"
ARTIFACT_ROOT = REPO / "artifacts" / "candidate-57-jump-arbitration-handoff-fresh-v1"
EVIDENCE_ROOT = HERE / "evidence" / "jump-arbitration-handoff-fresh-v1"
CACHE_ROOT = REPO / ".cache" / "candidate-57-jump-arbitration-handoff-fresh-v1"
START = date(2026, 4, 1)
END = date(2026, 4, 14)
CALENDAR_DAYS = (END - START).days + 1
CELLS = (
    ("source_max_z__no_handoff", "source_max_z", False),
    ("least_qualifying_z__no_handoff", "least_qualifying_z", False),
    ("source_max_z__deferred_handoff", "source_max_z", True),
    ("least_qualifying_z__deferred_handoff", "least_qualifying_z", True),
)
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any) -> float:
    if value is None:
        return math.nan
    match = _NUMBER.search(str(value).replace(",", "").replace("_", ""))
    if match is None:
        return math.nan
    try:
        result = float(match.group(0))
    except ValueError:
        return math.nan
    return result if math.isfinite(result) else math.nan


def quantiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"min": None, "q25": None, "median": None, "q75": None, "max": None}

    def q(fraction: float) -> float:
        position = fraction * (len(clean) - 1)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return clean[lower]
        weight = position - lower
        return clean[lower] * (1.0 - weight) + clean[upper] * weight

    return {
        "min": clean[0],
        "q25": q(0.25),
        "median": q(0.50),
        "q75": q(0.75),
        "max": clean[-1],
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def terminal_order(status: str) -> bool:
    return status.strip().upper() in {
        "FILLED",
        "CANCELED",
        "CANCELLED",
        "REJECTED",
        "DENIED",
        "EXPIRED",
    }


def make_config(cell: str, handoff: bool) -> Path:
    config = json.loads((C51 / "config.json").read_text(encoding="utf-8"))
    config = copy.deepcopy(config)
    strategy = config["strategy"]
    for key in (
        "sma_offset_low",
        "sma_offset_high",
        "sma_stop_min_fraction",
        "sma_stop_max_fraction",
        "sma_stop_atr_buffer",
    ):
        strategy.pop(key, None)
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 240,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "jump_timeframe_minutes": 240,
            "jump_threshold_sigma": 2.0,
            "jump_volatility_window": 18,
            "jump_min_absolute_return": 0.0,
            "jump_terminal_atr_period": 14,
            "jump_stop_atr_multiple": 1.0,
            "jump_min_stop_fraction": 0.0015,
            "jump_emergency_target_fraction": 0.20,
            "jump_stop_mode": "impulse",
            "jump_selection_mode": "source",
            "jump_min_residual_share": 0.50,
            "jump_min_residual_z": 0.75,
            "jump_confirmation_minutes": 0,
            "jump_confirmation_bucket_minutes": 5,
            "jump_protection_mode": "transient_be",
            "jump_protection_activation_r": 0.4,
            "jump_protection_floor_r": 0.0,
            "jump_protection_trail_gap_r": 999.0,
            "jump_protection_escape_r": 1.0,
            "jump_boundary_handoff_enabled": bool(handoff),
            "jump_boundary_handoff_expiry_minutes": 3,
        }
    )
    path = WORK_ROOT / cell / "config.json"
    dump(path, config)
    return path


def run_cell(cell: str, arbitration: str, handoff: bool) -> int:
    output = ARTIFACT_ROOT / cell
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    config = make_config(cell, handoff)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(config),
        "--start",
        START.isoformat(),
        "--end",
        END.isoformat(),
        "--cache",
        str(CACHE_ROOT),
        "--output",
        str(output),
        "--workspace",
        str(WORK_ROOT / cell / "workspace"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C51)
    env["C57_JUMP_ARBITRATION_MODE"] = arbitration
    completed = subprocess.run(command, cwd=REPO, env=env, check=False)
    return int(completed.returncode)


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def classify_exit(scenario: dict[str, Any], events: list[dict[str, Any]]) -> str:
    management = str(scenario.get("management_exit_reason") or "").upper()
    if "PROTECTION" in management:
        return "TRANSIENT_PROTECTION"
    if "SOURCE_HORIZON" in management:
        return "SOURCE_HORIZON"
    if "MAX_HOLD" in management:
        return "MAX_HOLD"
    scenario_id = str(scenario.get("scenario_id") or "")
    grouped = [
        event for event in events
        if str(event.get("scenario_id") or "") == scenario_id
    ]
    text = " ".join(
        str(event.get("event_type") or event.get("event") or "").upper()
        for event in grouped
    )
    if "FILL_RISK" in text or "POST_FILL" in text:
        return "FILL_RISK"
    if "STOP" in text:
        return "STRUCTURAL_STOP"
    if "TARGET" in text or "TAKE_PROFIT" in text:
        return "EMERGENCY_TARGET"
    if "SOURCE_HORIZON" in text or "MAX_HOLD" in text:
        return "SOURCE_HORIZON"
    return "OTHER_OR_REPORT_ONLY"


def summarize(cell: str, arbitration: str, handoff: bool, returncode: int) -> dict[str, Any]:
    source = ARTIFACT_ROOT / cell
    required = [
        source / "metrics.json",
        source / "strategy_diagnostics.json",
        source / "closed_scenarios.json",
        source / "scenario_events.jsonl",
        source / "positions.csv",
        source / "orders.csv",
    ]
    if returncode != 0 or not all(path.is_file() for path in required):
        return {
            "cell": cell,
            "arbitration": arbitration,
            "handoff": handoff,
            "produced": False,
            "returncode": returncode,
            "missing": [str(path) for path in required if not path.is_file()],
        }

    metrics = json.loads((source / "metrics.json").read_text(encoding="utf-8"))
    diagnostics = json.loads(
        (source / "strategy_diagnostics.json").read_text(encoding="utf-8")
    )
    scenarios: list[dict[str, Any]] = json.loads(
        (source / "closed_scenarios.json").read_text(encoding="utf-8")
    )
    events = load_events(source / "scenario_events.jsonl")
    positions = read_csv(source / "positions.csv")
    orders = read_csv(source / "orders.csv")

    pnl: list[float] = []
    r_values: list[float] = []
    holding_minutes: list[float] = []
    per_symbol: Counter[str] = Counter()
    per_side: Counter[str] = Counter()
    per_exit: Counter[str] = Counter()
    boundary_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    trade_rows: list[dict[str, Any]] = []
    handoff_trade_count = 0

    for scenario in scenarios:
        realized = number(scenario.get("realized_pnl"))
        risk = float(scenario.get("risk_budget") or math.nan)
        after_cost_r = (
            realized / risk
            if math.isfinite(realized) and math.isfinite(risk) and risk > 0.0
            else math.nan
        )
        pnl.append(realized)
        r_values.append(after_cost_r)
        symbol = str(scenario.get("symbol"))
        side = int(scenario.get("side") or 0)
        episode_ts = int(scenario.get("episode_ts") or 0)
        diagnostics_row = dict(scenario.get("diagnostics") or {})
        candidate_json = diagnostics_row.get("jump_boundary_candidate_set_json")
        candidates: list[dict[str, Any]] = []
        if candidate_json:
            try:
                candidates = json.loads(str(candidate_json))
            except json.JSONDecodeError:
                candidates = []
        exit_class = classify_exit(scenario, events)
        handoff_trade = bool(scenario.get("boundary_handoff"))
        handoff_trade_count += int(handoff_trade)
        per_symbol[symbol] += 1
        per_side["long" if side > 0 else "short"] += 1
        per_exit[exit_class] += 1
        row = {
            "scenario_id": scenario.get("scenario_id"),
            "symbol": symbol,
            "side": side,
            "episode_ts": episode_ts,
            "realized_pnl_usdt": realized,
            "risk_budget_usdt": risk,
            "after_cost_r": after_cost_r,
            "exit_class": exit_class,
            "boundary_handoff": handoff_trade,
            "boundary_handoff_delay_minutes": scenario.get(
                "boundary_handoff_delay_minutes"
            ),
            "effective_arbitration_mode": diagnostics_row.get(
                "jump_effective_arbitration_mode"
            ),
            "absolute_z": diagnostics_row.get("jump_absolute_z"),
            "source_score": diagnostics_row.get("jump_source_score"),
            "effective_score": diagnostics_row.get(
                "jump_effective_arbitration_score"
            ),
            "candidate_count": diagnostics_row.get(
                "jump_boundary_candidate_count", len(candidates) or 1
            ),
            "candidate_set": candidates,
            "management_exit_reason": scenario.get("management_exit_reason"),
        }
        trade_rows.append(row)
        boundary_rows[episode_ts].append(row)

    for position in positions:
        side = str(position.get("side", "")).strip().upper()
        closed = str(position.get("ts_closed", "")).strip().lower()
        if side != "FLAT" or closed in {"", "nan", "none", "nat"}:
            continue
        duration = number(position.get("duration_ns"))
        if math.isfinite(duration):
            holding_minutes.append(duration / 60_000_000_000.0)

    open_positions = [
        row for row in positions
        if str(row.get("side", "")).strip().upper() != "FLAT"
        or str(row.get("ts_closed", "")).strip().lower()
        in {"", "nan", "none", "nat"}
    ]
    active_orders = [
        row for row in orders
        if not terminal_order(str(row.get("status", "")))
    ]
    gross_profit = sum(value for value in pnl if math.isfinite(value) and value > 0.0)
    gross_loss = -sum(value for value in pnl if math.isfinite(value) and value < 0.0)
    wins = sum(value > 0.0 for value in pnl if math.isfinite(value))
    losses = sum(value < 0.0 for value in pnl if math.isfinite(value))
    event_counts = Counter(
        str(event.get("event_type") or event.get("event") or "UNKNOWN")
        for event in events
    )
    handoff_events = {
        key: value
        for key, value in sorted(event_counts.items())
        if "HANDOFF" in key.upper()
    }

    destination = EVIDENCE_ROOT / cell
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "metrics.json",
        "strategy_diagnostics.json",
        "closed_scenarios.json",
        "scenario_events.jsonl",
        "positions.csv",
        "orders.csv",
        "run.json",
        "data_manifest.json",
    ):
        candidate = source / name
        if candidate.is_file():
            shutil.copy2(candidate, destination / name)
    dump(destination / "trade_rows.json", trade_rows)

    checks = metrics.get("checks") or {}
    end_flat = not open_positions and not active_orders
    return {
        "cell": cell,
        "arbitration": arbitration,
        "handoff": handoff,
        "produced": True,
        "returncode": returncode,
        "engine": "NautilusTrader BacktestNode",
        "interval": [START.isoformat(), END.isoformat()],
        "calendar_days": CALENDAR_DAYS,
        "starting_nav": metrics.get("starting_nav"),
        "ending_nav": metrics.get("ending_nav"),
        "total_return": metrics.get("total_return"),
        "geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "max_drawdown": metrics.get("max_drawdown"),
        "completed_trades": len(scenarios),
        "completed_trades_per_day": len(scenarios) / CALENDAR_DAYS,
        "distinct_executed_boundaries": len(boundary_rows),
        "distinct_executed_boundaries_per_day": len(boundary_rows) / CALENDAR_DAYS,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(scenarios) if scenarios else 0.0,
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "sum_after_cost_r": sum(
            value for value in r_values if math.isfinite(value)
        ),
        "mean_after_cost_r": (
            sum(value for value in r_values if math.isfinite(value))
            / sum(math.isfinite(value) for value in r_values)
            if any(math.isfinite(value) for value in r_values)
            else None
        ),
        "after_cost_r_distribution": quantiles(r_values),
        "holding_minutes": quantiles(holding_minutes),
        "per_symbol": dict(sorted(per_symbol.items())),
        "per_side": dict(sorted(per_side.items())),
        "per_exit_class": dict(sorted(per_exit.items())),
        "handoff_trade_count": handoff_trade_count,
        "handoff_event_counts": handoff_events,
        "strategy_handoff_diagnostics": {
            key: diagnostics.get(key)
            for key in (
                "jump_boundary_handoff_enabled",
                "jump_boundary_handoff_frozen",
                "jump_boundary_handoff_submitted",
                "jump_boundary_handoff_expired",
                "jump_boundary_handoff_no_candidate",
                "jump_boundary_handoff_still_open",
            )
        },
        "strategy_source_candidates_visible_when_flat": diagnostics.get(
            "jump_source_candidates"
        ),
        "end_state": {
            "open_position_rows": len(open_positions),
            "active_order_rows": len(active_orders),
            "end_flat": end_flat,
            "relevant_runner_checks": {
                key: checks.get(key)
                for key in (
                    "closed_position_rows_match_trade_count",
                    "no_open_positions_at_end",
                    "no_active_orders_at_end",
                    "single_entry_intent",
                    "single_position",
                    "no_global_position_violation",
                )
                if key in checks
            },
        },
        "trade_rows": trade_rows,
    }


def trade_index(summary: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(row["symbol"]), int(row["episode_ts"])): row
        for row in summary.get("trade_rows", [])
    }


def compare_pair(
    left_name: str,
    right_name: str,
    summaries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    left = trade_index(summaries[left_name])
    right = trade_index(summaries[right_name])
    shared = sorted(set(left) & set(right))
    left_only = sorted(set(left) - set(right))
    right_only = sorted(set(right) - set(left))
    changed = []
    for key in shared:
        lrow = left[key]
        rrow = right[key]
        lvalue = float(lrow.get("after_cost_r") or 0.0)
        rvalue = float(rrow.get("after_cost_r") or 0.0)
        if abs(lvalue - rvalue) > 1e-9 or lrow.get("exit_class") != rrow.get("exit_class"):
            changed.append(
                {
                    "symbol": key[0],
                    "episode_ts": key[1],
                    "left_r": lrow.get("after_cost_r"),
                    "right_r": rrow.get("after_cost_r"),
                    "left_exit": lrow.get("exit_class"),
                    "right_exit": rrow.get("exit_class"),
                }
            )
    return {
        "left": left_name,
        "right": right_name,
        "shared_trade_keys": len(shared),
        "left_only": [
            {"symbol": key[0], "episode_ts": key[1], **left[key]}
            for key in left_only
        ],
        "right_only": [
            {"symbol": key[0], "episode_ts": key[1], **right[key]}
            for key in right_only
        ],
        "shared_changed_outcomes": changed,
        "left_total_return": summaries[left_name].get("total_return"),
        "right_total_return": summaries[right_name].get("total_return"),
        "left_sum_r": summaries[left_name].get("sum_after_cost_r"),
        "right_sum_r": summaries[right_name].get("sum_after_cost_r"),
    }


def markdown(comparison: dict[str, Any]) -> str:
    rows = []
    for cell, _, _ in CELLS:
        item = comparison["cells"][cell]
        if not item.get("produced"):
            rows.append(f"| {cell} | implementation failure | - | - | - | - | - |")
            continue
        pf = item.get("profit_factor")
        rows.append(
            "| {cell} | {trades} | {wins}/{losses} | {pf} | {ret:.3%} | {geo:.3%} | {mdd:.3%} |".format(
                cell=cell,
                trades=item["completed_trades"],
                wins=item["wins"],
                losses=item["losses"],
                pf="∞" if pf is None else f"{float(pf):.3f}",
                ret=float(item.get("total_return") or 0.0),
                geo=float(item.get("geometric_daily_growth") or 0.0),
                mdd=float(item.get("max_drawdown") or 0.0),
            )
        )
    return """# 4h jump arbitration × boundary-handoff result

This is a causal mechanism experiment, not a pass/fail gate.  The interval is
now development data.  Read each cell's `trade_rows.json`, scenario events and
orders before modifying the selector or handoff.

| cell | trades | W/L | PF | total return | geo/day | MDD |
|---|---:|---:|---:|---:|---:|---:|
{rows}

A raw symbol candidate count is not an independent opportunity count.  Same
4-hour boundary candidates must remain grouped, and handoff value must be read
from the continuous one-slot account path.
""".format(rows="\n".join(rows))


def main() -> int:
    freeze = HERE / "JUMP_ARBITRATION_HANDOFF_FRESH_V1_FREEZE.md"
    if not freeze.is_file():
        raise RuntimeError("frozen experiment specification missing")
    if EVIDENCE_ROOT.exists():
        shutil.rmtree(EVIDENCE_ROOT)
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    summaries: dict[str, dict[str, Any]] = {}
    process_status = 0
    for cell, arbitration, handoff in CELLS:
        code = run_cell(cell, arbitration, handoff)
        process_status = process_status or code
        summaries[cell] = summarize(cell, arbitration, handoff, code)

    pairwise = {
        "arbitration_without_handoff": compare_pair(
            "source_max_z__no_handoff",
            "least_qualifying_z__no_handoff",
            summaries,
        ),
        "arbitration_with_handoff": compare_pair(
            "source_max_z__deferred_handoff",
            "least_qualifying_z__deferred_handoff",
            summaries,
        ),
        "handoff_under_source_max_z": compare_pair(
            "source_max_z__no_handoff",
            "source_max_z__deferred_handoff",
            summaries,
        ),
        "handoff_under_least_z": compare_pair(
            "least_qualifying_z__no_handoff",
            "least_qualifying_z__deferred_handoff",
            summaries,
        ),
    }
    comparison = {
        "experiment": "candidate-57-jump-arbitration-handoff-fresh-v1",
        "binary_gate": False,
        "fresh_interval_consumed": True,
        "interval": [START.isoformat(), END.isoformat()],
        "cells": summaries,
        "pairwise": pairwise,
    }
    dump(EVIDENCE_ROOT / "comparison.json", comparison)
    (EVIDENCE_ROOT / "RESULT.md").write_text(
        markdown(comparison), encoding="utf-8"
    )
    print(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False))

    if process_status != 0:
        return process_status
    for cell, _, _ in CELLS:
        if not summaries[cell].get("produced"):
            return 1
        if not (summaries[cell].get("end_state") or {}).get("end_flat"):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
