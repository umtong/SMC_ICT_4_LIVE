#!/usr/bin/env python3
"""Select the next action across IRX, microstructure, and cross-market families."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_decision_v2 import family_state, gate, load

ROOT = Path(__file__).resolve().parent
TERMINAL_LOGIC_FAILURES = {
    "SELECTION_FAILURE_AFTER_EXECUTION",
    "ALPHA_OR_FREQUENCY_GATE_FAILURE",
    "FREQUENCY_FAILURE_AFTER_VALID_PLANS",
    "ONTOLOGY_FAILURE_NO_DETECTABLE_AUCTIONS",
    "CLASSIFICATION_FAILURE_AFTER_DETECTABLE_AUCTIONS",
    "CONFIRMATION_OR_TARGET_FAILURE",
    "EXECUTION_FAILURE_NO_CLOSED_TRADES",
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
    cross = load(ROOT / "results" / "CROSS_MARKET" / "summary.json")

    families = {
        "pool_impact": family_state("pool_impact", micro1, diagnoses.get("MICROSTRUCTURE"), "M1"),
        "vwap_exhaustion": family_state("vwap_exhaustion", micro2, diagnoses.get("MICROSTRUCTURE_V2"), "M4"),
        "balance_acceptance": family_state("balance_acceptance", micro3, diagnoses.get("MICROSTRUCTURE_V3"), "M7"),
        "cross_market_leader_follower": family_state(
            "cross_market_leader_follower",
            cross,
            diagnoses.get("CROSS_MARKET"),
            "C1",
        ),
    }
    cross_check = diagnoses.get("CROSS_MARKET_CHECK") if isinstance(diagnoses.get("CROSS_MARKET_CHECK"), dict) else {}
    cross_check_passed = cross_check.get("passed") is True

    if gate(irx_long, "long_gate_passed"):
        next_action = "RUN_PREDECLARED_EXECUTION_STRESS_ON_IRX_LONG_WINNER"
    elif gate(irx_holdout, "holdout_gate_passed"):
        next_action = "RUN_PREDECLARED_90_DAY_IRX_CONTINUOUS_EVALUATION"
    elif irx_matrix is not None and irx_matrix.get("selected_variant") is not None:
        next_action = "RUN_FROZEN_IRX_UNTOUCHED_HOLDOUTS"
    elif families["cross_market_leader_follower"]["status"] == "THREE_WEEK_GATE_PASSED":
        next_action = "FREEZE_CROSS_MARKET_UNTOUCHED_HOLDOUTS"
    elif any(
        families[name]["status"] == "THREE_WEEK_GATE_PASSED"
        for name in ("pool_impact", "vwap_exhaustion", "balance_acceptance")
    ):
        next_action = "PORT_WINNING_MICROSTRUCTURE_FAMILY_TO_FOUR_MARKETS"
    elif any(value["status"] == "IMPLEMENTATION_FAILURE" for value in families.values()):
        next_action = "REPAIR_IMPLEMENTATION_WITHOUT_ALPHA_CHANGE"
    elif cross_check and not cross_check_passed:
        next_action = "REPAIR_CROSS_MARKET_IMPLEMENTATION_BEFORE_DATA"
    else:
        micro_states = {
            families[name]["status"]
            for name in ("pool_impact", "vwap_exhaustion", "balance_acceptance")
        }
        balance_state = families["balance_acceptance"]["status"]
        cross_state = families["cross_market_leader_follower"]["status"]
        predecessor_states = {
            families["pool_impact"]["status"],
            families["vwap_exhaustion"]["status"],
        }
        if balance_state.startswith("NOT_RUN") and all(
            state not in {"WAITING_FOR_EVIDENCE", "IMPLEMENTATION_FAILURE"}
            for state in predecessor_states
        ):
            next_action = "RUN_FROZEN_BALANCE_ACCEPTANCE_WEEKS"
        elif all(state in TERMINAL_LOGIC_FAILURES for state in micro_states):
            if cross_state == "THREE_WEEK_GATE_PASSED":
                next_action = "FREEZE_CROSS_MARKET_UNTOUCHED_HOLDOUTS"
            elif cross_state in TERMINAL_LOGIC_FAILURES:
                next_action = "OPEN_CAUSAL_VOLATILITY_STATE_ROUTER_FAMILY"
            elif cross_check_passed:
                next_action = "RUN_FROZEN_CROSS_MARKET_WEEKS"
            else:
                next_action = "WAIT_FOR_CROSS_MARKET_IMPLEMENTATION_CHECK"
        else:
            next_action = "WAIT_FOR_ACTIVE_RESEARCH_EVIDENCE"

    output = {
        "schema": "candidate-11-research-decision-v3",
        "irx": {
            "matrix_selected_variant": None if irx_matrix is None else irx_matrix.get("selected_variant"),
            "untouched_holdout_passed": gate(irx_holdout, "holdout_gate_passed"),
            "continuous_90d_passed": gate(irx_long, "long_gate_passed"),
        },
        "families": families,
        "cross_market_implementation_check_passed": cross_check_passed,
        "next_action": next_action,
        "success_claim": False,
    }
    path = ROOT / "results" / "RESEARCH_DECISION.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
