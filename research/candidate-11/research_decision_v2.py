#!/usr/bin/env python3
"""Select the next Candidate 11 action from all committed research evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def gate(summary: dict[str, Any] | None, *names: str) -> bool:
    if summary is None:
        return False
    return any(summary.get(name) is True for name in names)


def first_week(summary: dict[str, Any] | None, week: str) -> dict[str, Any]:
    if summary is None:
        return {}
    weeks = summary.get("weeks")
    if not isinstance(weeks, dict):
        return {}
    value = weeks.get(week)
    return value if isinstance(value, dict) else {}


def family_state(
    name: str,
    summary: dict[str, Any] | None,
    diagnosis: dict[str, Any] | None,
    first: str,
) -> dict[str, Any]:
    if summary is None:
        execution = None if diagnosis is None else diagnosis.get("execution_classification")
        return {
            "name": name,
            "status": "WAITING_FOR_EVIDENCE" if execution in (None, "EXECUTION_COMPLETED") else "IMPLEMENTATION_FAILURE",
            "execution_classification": execution,
        }
    if gate(summary, "three_week_gate_passed"):
        return {"name": name, "status": "THREE_WEEK_GATE_PASSED"}
    status = str(summary.get("status") or "")
    if status.startswith("NOT_RUN"):
        return {"name": name, "status": status}
    metrics = first_week(summary, first)
    plans = int(metrics.get("submitted_plans") or 0)
    trades = int(metrics.get("closed_trades") or 0)
    losses = int(metrics.get("losses") or 0)
    events = metrics.get("detector_event_counts") if isinstance(metrics.get("detector_event_counts"), dict) else {}
    if diagnosis is not None:
        execution = diagnosis.get("execution_classification")
        if execution not in (None, "EXECUTION_COMPLETED"):
            return {
                "name": name,
                "status": "IMPLEMENTATION_FAILURE",
                "execution_classification": execution,
                "error_tail": diagnosis.get("error_tail") or [],
            }
    if plans > 0 and trades == 0:
        state = "EXECUTION_FAILURE_NO_CLOSED_TRADES"
    elif trades > 0 and losses > 0:
        state = "SELECTION_FAILURE_AFTER_EXECUTION"
    elif trades > 0:
        state = "ALPHA_OR_FREQUENCY_GATE_FAILURE"
    elif plans > 0:
        state = "FREQUENCY_FAILURE_AFTER_VALID_PLANS"
    else:
        classifications = sum(
            int(events.get(key) or 0)
            for key in (
                "AGGRESSOR_FLOW_ABSORBED",
                "EXTERNAL_POOL_EFFICIENTLY_ACCEPTED",
                "AGGRESSOR_EXHAUSTION_DETECTED",
                "BALANCE_BREAKOUT_MULTI_CLOSE_ACCEPTED",
                "BALANCE_BOUNDARY_RETEST_HELD",
            )
        )
        accesses = sum(
            int(events.get(key) or 0)
            for key in (
                "EXTERNAL_POOL_FIRST_ACCESSED",
                "FIVE_MINUTE_BALANCE_COMPLETED",
            )
        )
        if accesses == 0:
            state = "ONTOLOGY_FAILURE_NO_DETECTABLE_AUCTIONS"
        elif classifications == 0:
            state = "CLASSIFICATION_FAILURE_AFTER_DETECTABLE_AUCTIONS"
        else:
            state = "CONFIRMATION_OR_TARGET_FAILURE"
    return {
        "name": name,
        "status": state,
        "first_week": first,
        "submitted_plans": plans,
        "closed_trades": trades,
        "losses": losses,
        "event_counts": events,
    }


def main() -> None:
    diagnosis_payload = load(ROOT / "results" / "RESEARCH_DIAGNOSIS.json") or {}
    diagnoses = diagnosis_payload.get("results") if isinstance(diagnosis_payload.get("results"), dict) else {}
    irx_matrix = load(ROOT / "results" / "IRX_MATRIX" / "summary.json")
    irx_holdout = load(ROOT / "results" / "IRX_HOLDOUT" / "summary.json")
    irx_long = load(ROOT / "results" / "IRX_LONG" / "summary.json")
    micro1 = load(ROOT / "results" / "MICROSTRUCTURE" / "summary.json")
    micro2 = load(ROOT / "results" / "MICROSTRUCTURE_V2" / "summary.json")
    micro3 = load(ROOT / "results" / "MICROSTRUCTURE_V3" / "summary.json")

    families = {
        "pool_impact": family_state("pool_impact", micro1, diagnoses.get("MICROSTRUCTURE"), "M1"),
        "vwap_exhaustion": family_state("vwap_exhaustion", micro2, diagnoses.get("MICROSTRUCTURE_V2"), "M4"),
        "balance_acceptance": family_state("balance_acceptance", micro3, diagnoses.get("MICROSTRUCTURE_V3"), "M7"),
    }

    if gate(irx_long, "long_gate_passed"):
        next_action = "RUN_PREDECLARED_EXECUTION_STRESS_ON_IRX_LONG_WINNER"
    elif gate(irx_holdout, "holdout_gate_passed"):
        next_action = "RUN_PREDECLARED_90_DAY_IRX_CONTINUOUS_EVALUATION"
    elif irx_matrix is not None and irx_matrix.get("selected_variant") is not None:
        next_action = "RUN_FROZEN_IRX_UNTOUCHED_HOLDOUTS"
    elif any(value["status"] == "THREE_WEEK_GATE_PASSED" for value in families.values()):
        next_action = "PORT_WINNING_MICROSTRUCTURE_FAMILY_TO_FOUR_MARKETS"
    elif any(value["status"] == "IMPLEMENTATION_FAILURE" for value in families.values()):
        next_action = "REPAIR_IMPLEMENTATION_WITHOUT_ALPHA_CHANGE"
    elif families["balance_acceptance"]["status"].startswith("NOT_RUN"):
        predecessor_states = {
            families["pool_impact"]["status"],
            families["vwap_exhaustion"]["status"],
        }
        if all(state not in {"WAITING_FOR_EVIDENCE", "IMPLEMENTATION_FAILURE"} for state in predecessor_states):
            next_action = "RUN_FROZEN_BALANCE_ACCEPTANCE_WEEKS"
        else:
            next_action = "WAIT_FOR_ACTIVE_PREDECESSOR_EVIDENCE"
    elif all(value["status"] in {
        "SELECTION_FAILURE_AFTER_EXECUTION",
        "ALPHA_OR_FREQUENCY_GATE_FAILURE",
        "FREQUENCY_FAILURE_AFTER_VALID_PLANS",
        "ONTOLOGY_FAILURE_NO_DETECTABLE_AUCTIONS",
        "CLASSIFICATION_FAILURE_AFTER_DETECTABLE_AUCTIONS",
        "CONFIRMATION_OR_TARGET_FAILURE",
        "EXECUTION_FAILURE_NO_CLOSED_TRADES",
    } for value in families.values()):
        next_action = "REPLACE_BTC_MICROSTRUCTURE_SUITE_WITH_CROSS_MARKET_CAUSAL_LEADER_FAMILY"
    else:
        next_action = "WAIT_FOR_ACTIVE_RESEARCH_EVIDENCE"

    output = {
        "schema": "candidate-11-research-decision-v2",
        "irx": {
            "matrix_selected_variant": None if irx_matrix is None else irx_matrix.get("selected_variant"),
            "untouched_holdout_passed": gate(irx_holdout, "holdout_gate_passed"),
            "continuous_90d_passed": gate(irx_long, "long_gate_passed"),
        },
        "microstructure_families": families,
        "next_action": next_action,
        "success_claim": False,
    }
    path = ROOT / "results" / "RESEARCH_DECISION.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
