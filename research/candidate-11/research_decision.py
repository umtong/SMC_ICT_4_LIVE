#!/usr/bin/env python3
"""Derive the next alpha decision from committed causal evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def micro_decision(summary: dict[str, Any] | None, first_week: str) -> dict[str, Any]:
    if summary is None:
        return {"status": "WAITING_FOR_EVIDENCE"}
    if summary.get("three_week_gate_passed") is True:
        return {"status": "ADVANCE_TO_PORTFOLIO_INTEGRATION"}
    weeks = summary.get("weeks") if isinstance(summary.get("weeks"), dict) else {}
    metrics = weeks.get(first_week) if isinstance(weeks.get(first_week), dict) else {}
    errors = metrics.get("engine_errors") or []
    if errors:
        return {"status": "IMPLEMENTATION_FAILURE", "engine_errors": errors}
    plans = int(metrics.get("submitted_plans") or 0)
    trades = int(metrics.get("closed_trades") or 0)
    losses = int(metrics.get("losses") or 0)
    events = metrics.get("detector_event_counts") if isinstance(metrics.get("detector_event_counts"), dict) else {}
    pool_accesses = int(events.get("EXTERNAL_POOL_FIRST_ACCESSED") or 0)
    absorption = int(events.get("AGGRESSOR_FLOW_ABSORBED") or 0)
    acceptance = int(events.get("EXTERNAL_POOL_EFFICIENTLY_ACCEPTED") or 0)
    exhaustion = int(events.get("AGGRESSOR_EXHAUSTION_DETECTED") or 0)
    emitted = sum(
        int(events.get(name) or 0)
        for name in (
            "ABSORPTION_REVERSAL_PLAN_EMITTED",
            "EFFICIENT_ACCEPTANCE_PLAN_EMITTED",
            "AGGRESSOR_EXHAUSTION_PLAN_EMITTED",
        )
    )
    if plans and not trades:
        status = "EXECUTION_FAILURE_NO_CLOSED_TRADES"
    elif trades and losses:
        status = "SELECTION_FAILURE_AFTER_EXECUTION"
    elif plans or emitted:
        status = "FREQUENCY_FAILURE_AFTER_VALID_PLANS"
    elif pool_accesses == 0 and exhaustion == 0:
        status = "ONTOLOGY_FAILURE_NO_DETECTABLE_AUCTIONS"
    elif pool_accesses and not (absorption or acceptance):
        status = "POOL_ACCESS_CLASSIFICATION_FAILURE"
    elif (absorption or acceptance or exhaustion) and not emitted:
        status = "CONFIRMATION_OR_STRUCTURAL_TARGET_FAILURE"
    else:
        status = "LOGIC_OR_FREQUENCY_FAILURE"
    return {
        "status": status,
        "first_week": first_week,
        "submitted_plans": plans,
        "closed_trades": trades,
        "losses": losses,
        "event_funnel": {
            "pool_accesses": pool_accesses,
            "absorption_classifications": absorption,
            "acceptance_classifications": acceptance,
            "exhaustion_classifications": exhaustion,
            "emitted_plans": emitted,
        },
    }


def main() -> None:
    irx = load(ROOT / "results" / "IRX_MATRIX" / "summary.json")
    irx_holdout = load(ROOT / "results" / "IRX_HOLDOUT" / "summary.json")
    micro = load(ROOT / "results" / "MICROSTRUCTURE" / "summary.json")
    micro_v2 = load(ROOT / "results" / "MICROSTRUCTURE_V2" / "summary.json")
    irx_status = "WAITING_FOR_EVIDENCE"
    if irx is not None:
        if irx_holdout is not None and irx_holdout.get("holdout_gate_passed") is True:
            irx_status = "ADVANCE_TO_CONTINUOUS_EVALUATION"
        elif irx.get("selected_variant") is not None:
            irx_status = "WAITING_FOR_UNTOUCHED_HOLDOUT"
        else:
            irx_status = "REJECTED_NO_ELIGIBLE_VARIANT"
    decisions = {
        "schema": "candidate-11-research-decision-v1",
        "irx": {"status": irx_status},
        "microstructure_pool_impact": micro_decision(micro, "M1"),
        "microstructure_vwap_exhaustion": micro_decision(micro_v2, "M4"),
        "success_claim": False,
    }
    statuses = {
        decisions["irx"]["status"],
        decisions["microstructure_pool_impact"]["status"],
        decisions["microstructure_vwap_exhaustion"]["status"],
    }
    if any(status.startswith("ADVANCE") for status in statuses):
        next_action = "RUN_ONLY_THE_PRECOMMITTED_ADVANCEMENT"
    elif any("IMPLEMENTATION" in status or "EXECUTION_FAILURE" in status for status in statuses):
        next_action = "REPAIR_IMPLEMENTATION_BEFORE_ALPHA_CHANGE"
    elif any("SELECTION_FAILURE" in status for status in statuses):
        next_action = "REPLACE_SELECTION_STATE_NOT_RISK_OR_COSTS"
    elif any("CONFIRMATION_OR_STRUCTURAL_TARGET_FAILURE" in status for status in statuses):
        next_action = "REPLACE_CONFIRMATION_TARGET_PAIR"
    elif any("ONTOLOGY_FAILURE" in status or "CLASSIFICATION_FAILURE" in status for status in statuses):
        next_action = "OPEN_MULTI_HORIZON_IMPACT_CONTINUATION_FAMILY"
    else:
        next_action = "WAIT_FOR_RUNNING_EVIDENCE"
    decisions["next_action"] = next_action
    output = ROOT / "results" / "RESEARCH_DECISION.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(decisions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
