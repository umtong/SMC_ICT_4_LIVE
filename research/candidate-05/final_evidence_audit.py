#!/usr/bin/env python3
"""Independently audit end-to-end evidence and global entry lifecycle.

A 91-day screen can promote a candidate but cannot satisfy the project contract.
The final performance audit is fixed to 2024-01-01 through 2026-06-30.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


PROJECT_PASS = "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS"
LONG_DAYS = 912
LONG_MIN_TRADES = math.ceil(45 * LONG_DAYS / 91)
LONG_MIN_WINS = math.ceil(15 * LONG_DAYS / 91)
LONG_MIN_ACTIVE_DAYS = math.ceil(30 * LONG_DAYS / 91)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("end-to-end evidence must be an object")
    return value


def _number(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _integer(value: Any, default: int) -> int:
    return int(_number(value, float(default)))


def _monthly_returns(daily_returns: Any) -> dict[str, float]:
    if not isinstance(daily_returns, dict):
        return {}
    multiples: dict[str, float] = {}
    for day, raw_return in sorted(daily_returns.items()):
        text = str(day)
        value = _number(raw_return, float("nan"))
        if len(text) < 7 or not math.isfinite(value) or value <= -1.0:
            return {}
        month = text[:7]
        multiples[month] = multiples.get(month, 1.0) * (1.0 + value)
    return {month: multiple - 1.0 for month, multiple in multiples.items()}


def audit_slot_run(run: Any) -> dict[str, Any]:
    if not isinstance(run, dict):
        return {"available": False, "audit_pass": False, "reason": "RUN_NOT_OBJECT"}
    trades = _integer(run.get("trades"), 0)
    slot = run.get("global_slot_audit")
    if not isinstance(slot, dict):
        return {"available": True, "audit_pass": False, "reason": "MISSING_SLOT_AUDIT", "trades": trades}
    checks = {
        "run_integrity_pass": bool(run.get("integrity_pass", False)),
        "slot_audit_pass": bool(slot.get("audit_pass", False)),
        "max_unfilled_entry_intents": _integer(slot.get("max_unfilled_entry_intents_replayed"), 99) <= 1,
        "max_open_positions": _integer(slot.get("max_open_positions_replayed"), 99) <= 1,
        "max_sum": _integer(slot.get("max_entry_intents_plus_positions_replayed"), 99) <= 1,
        "positions_opened_equal_trades": _integer(slot.get("positions_opened"), -1) == trades,
        "positions_closed_equal_trades": _integer(slot.get("positions_closed"), -1) == trades,
        "mismatches_zero": _integer(slot.get("mismatches"), 1) == 0,
        "release_phase_mismatches_zero": _integer(slot.get("release_phase_mismatches"), 1) == 0,
        "idle_at_end": bool(slot.get("idle_at_end", False)),
        "violations_empty": isinstance(slot.get("violations"), list) and not slot.get("violations"),
    }
    return {
        "available": True,
        "trades": trades,
        "slot": slot,
        "checks": checks,
        "audit_pass": all(checks.values()),
    }


def audit_final_gate(shared: dict[str, Any]) -> dict[str, Any]:
    runs = shared.get("runs")
    if not isinstance(runs, dict):
        return {"audit_pass": False, "reason": "MISSING_SHARED_RUNS"}
    long_run = runs.get("long-2024-2026h1")
    if not isinstance(long_run, dict):
        return {"audit_pass": False, "reason": "MISSING_LONG_2024_2026H1_RUN"}
    stage = long_run.get("stage") if isinstance(long_run.get("stage"), dict) else {}
    monthly = _monthly_returns(long_run.get("daily_returns"))
    positive_months = sum(value > 0.0 for value in monthly.values())
    checks = {
        "evaluation_start": stage.get("evaluation_start") == "2024-01-01",
        "evaluation_end": stage.get("evaluation_end") == "2026-06-30",
        "calendar_days": _integer(stage.get("calendar_days"), 0) == LONG_DAYS,
        "geometric_daily_growth": _number(long_run.get("geometric_daily_growth"), -1.0) >= 0.01,
        "trades": _integer(long_run.get("trades"), 0) >= LONG_MIN_TRADES,
        "wins": _integer(long_run.get("wins"), 0) >= LONG_MIN_WINS,
        "win_rate": _number(long_run.get("win_rate"), 0.0) >= 0.30,
        "active_days": _integer(long_run.get("active_days"), 0) >= LONG_MIN_ACTIVE_DAYS,
        "largest_winner_share": _number(long_run.get("largest_winner_share"), 1.0) <= 0.10,
        "max_drawdown_recoverable": _number(long_run.get("max_drawdown"), 1.0) <= 0.35,
        "positive_min_equity": _number(long_run.get("min_equity"), 0.0) > 0.0,
        "all_30_months_present": len(monthly) == 30,
        "at_least_18_positive_months": positive_months >= 18,
    }
    return {
        "checks": checks,
        "monthly_returns": monthly,
        "positive_months": positive_months,
        "audit_pass": all(checks.values()),
    }


def audit_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    shared = evidence.get("shared_account")
    if not isinstance(shared, dict):
        shared = {}
    runs = shared.get("runs") if isinstance(shared.get("runs"), dict) else {}
    stage_audits = {name: audit_slot_run(run) for name, run in runs.items()}
    lifecycle_pass = bool(stage_audits) and all(
        value.get("audit_pass", False) for value in stage_audits.values()
    )
    final_gate = audit_final_gate(shared)
    reported_project_pass = evidence.get("classification") == PROJECT_PASS

    if reported_project_pass and lifecycle_pass and final_gate.get("audit_pass"):
        classification = "AUDITED_PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS"
        audited_pass = True
        next_action = (
            "Freeze the audited 912-day shared strategy and proceed to live-adapter and paper-trading implementation without changing signal or risk logic."
        )
    elif reported_project_pass and not lifecycle_pass:
        classification = "IMPLEMENTATION_OR_EVIDENCE_ERROR_GLOBAL_LIFECYCLE_AUDIT"
        audited_pass = False
        next_action = (
            "Repair lifecycle evidence or an uncovered entry path, then rerun the identical shared-account range before accepting performance."
        )
    elif reported_project_pass and not final_gate.get("audit_pass"):
        classification = "EVIDENCE_ERROR_REPORTED_PASS_FAILED_LONG_RECOMPUTED_GATE"
        audited_pass = False
        next_action = (
            "Repair the long-horizon evidence or gate calculation and rerun 2024-01-01 through 2026-06-30."
        )
    else:
        classification = str(evidence.get("classification", "PROJECT_NOT_PASSED"))
        audited_pass = False
        next_action = evidence.get("next_action")

    return {
        "schema": "candidate-05-final-evidence-audit-v2",
        "source_commit": evidence.get("source_commit"),
        "workflow_run_id": evidence.get("workflow_run_id"),
        "reported_classification": evidence.get("classification"),
        "classification": classification,
        "audited_project_goal_passed": audited_pass,
        "selected_strategy": evidence.get("winner") if audited_pass else None,
        "lifecycle_audit_pass": lifecycle_pass,
        "stage_lifecycle_audits": stage_audits,
        "recomputed_final_gate": final_gate,
        "next_action": next_action,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_evidence(load(args.input.resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
