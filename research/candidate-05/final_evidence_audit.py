#!/usr/bin/env python3
"""Independently audit end-to-end v2 evidence and the global slot lifecycle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_PASS = "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("end-to-end evidence must be an object")
    return value


def audit_slot_run(run: Any) -> dict[str, Any]:
    if not isinstance(run, dict):
        return {"available": False, "audit_pass": False, "reason": "RUN_NOT_OBJECT"}
    trades = int(run.get("trades", 0) or 0)
    slot = run.get("global_slot_audit")
    if not isinstance(slot, dict):
        return {"available": True, "audit_pass": False, "reason": "MISSING_SLOT_AUDIT", "trades": trades}
    checks = {
        "run_integrity_pass": bool(run.get("integrity_pass", False)),
        "slot_audit_pass": bool(slot.get("audit_pass", False)),
        "max_unfilled_entry_intents": int(slot.get("max_unfilled_entry_intents_replayed", 99) or 99) <= 1,
        "max_open_positions": int(slot.get("max_open_positions_replayed", 99) or 99) <= 1,
        "max_sum": int(slot.get("max_entry_intents_plus_positions_replayed", 99) or 99) <= 1,
        "positions_opened_equal_trades": int(slot.get("positions_opened", -1) or 0) == trades,
        "positions_closed_equal_trades": int(slot.get("positions_closed", -1) or 0) == trades,
        "mismatches_zero": int(slot.get("mismatches", 1) or 0) == 0,
        "release_phase_mismatches_zero": int(slot.get("release_phase_mismatches", 1) or 0) == 0,
        "idle_at_end": bool(slot.get("idle_at_end", False)),
        "violations_empty": not slot.get("violations", ["missing"]),
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
    long_run = runs.get("continuous-91d")
    if not isinstance(long_run, dict):
        return {"audit_pass": False, "reason": "MISSING_91D_RUN"}
    checks = {
        "geometric_daily_growth": float(long_run.get("geometric_daily_growth", -1.0) or -1.0) >= 0.01,
        "trades": int(long_run.get("trades", 0) or 0) >= 45,
        "wins": int(long_run.get("wins", 0) or 0) >= 15,
        "win_rate": float(long_run.get("win_rate", 0.0) or 0.0) >= 0.30,
        "active_days": int(long_run.get("active_days", 0) or 0) >= 30,
        "largest_winner_share": float(long_run.get("largest_winner_share", 1.0) or 1.0) <= 0.25,
        "max_drawdown_recoverable": float(long_run.get("max_drawdown", 1.0) or 1.0) <= 0.35,
        "positive_min_equity": float(long_run.get("min_equity", 0.0) or 0.0) > 0.0,
    }
    return {"checks": checks, "audit_pass": all(checks.values())}


def audit_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    shared = evidence.get("shared_account")
    if not isinstance(shared, dict):
        shared = {}
    runs = shared.get("runs") if isinstance(shared.get("runs"), dict) else {}
    stage_audits = {
        name: audit_slot_run(run)
        for name, run in runs.items()
    }
    lifecycle_pass = bool(stage_audits) and all(
        value.get("audit_pass", False)
        for value in stage_audits.values()
    )
    final_gate = audit_final_gate(shared)
    reported_project_pass = evidence.get("classification") == PROJECT_PASS

    if reported_project_pass and lifecycle_pass and final_gate.get("audit_pass"):
        classification = "AUDITED_PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS"
        audited_pass = True
        next_action = (
            "Freeze the audited shared strategy and proceed to live-adapter and paper-trading implementation without changing signal or risk logic."
        )
    elif reported_project_pass and not lifecycle_pass:
        classification = "IMPLEMENTATION_OR_EVIDENCE_ERROR_GLOBAL_LIFECYCLE_AUDIT"
        audited_pass = False
        next_action = (
            "Repair lifecycle evidence or an uncovered entry path, then rerun the identical shared-account range before accepting performance."
        )
    elif reported_project_pass and not final_gate.get("audit_pass"):
        classification = "EVIDENCE_ERROR_REPORTED_PASS_FAILED_RECOMPUTED_GATE"
        audited_pass = False
        next_action = "Repair the final gate calculation and rerun the identical evidence audit."
    else:
        classification = str(evidence.get("classification", "PROJECT_NOT_PASSED"))
        audited_pass = False
        next_action = evidence.get("next_action")

    return {
        "schema": "candidate-05-final-evidence-audit-v1",
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
