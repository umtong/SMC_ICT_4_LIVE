#!/usr/bin/env python3
"""Validate Candidate 18 v8 execution and basis-state causality."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from validate_v6 import validate as validate_v6


def validate(root: Path, period: str) -> dict[str, Any]:
    decision = validate_v6(root, period)
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    diagnostics = json.loads(
        (root / "strategy_diagnostics.json").read_text(encoding="utf-8"),
    )
    events = [
        json.loads(line)
        for line in (root / "scenario_events.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
        if line.strip()
    ]
    entries = [event for event in events if event.get("event_type") == "ENTRY_SUBMITTED"]
    rejection_entries = [
        event for event in entries if event.get("details", {}).get("branch") == "REJECTION"
    ]
    acceptance_entries = [
        event for event in entries if event.get("details", {}).get("branch") == "ACCEPTANCE"
    ]
    malformed_basis = []
    shock_entries = []
    for event in rejection_entries:
        route = event.get("details", {}).get("candidate18_v8_basis_route", {})
        try:
            decision_name = str(route["decision"])
            premium = float(route["premium_index"])
            side = int(event["details"]["side"])
            age = float(route["premium_age_seconds"])
            observed = int(route["basis_observed_time_ns"])
            event_time = int(event["event_time_ns"])
        except (KeyError, TypeError, ValueError):
            malformed_basis.append(event.get("scenario_id"))
            continue
        if decision_name != "ENTER_SUSTAINED_BASIS_FADE":
            malformed_basis.append(event.get("scenario_id"))
        if not math.isfinite(premium) or side * premium >= 0.0:
            malformed_basis.append(event.get("scenario_id"))
        if not math.isfinite(age) or age < 0.0 or observed > event_time:
            malformed_basis.append(event.get("scenario_id"))
        quality = event.get("details", {}).get("candidate18_initiative_quality", {})
        if quality.get("route") == "SHOCK":
            shock_entries.append(event.get("scenario_id"))

    checks = dict(decision["checks"])
    checks.update(
        {
            "v8_candidate_recorded": metrics.get("candidate")
            == "candidate-18-v8-basis-dislocation-router",
            "has_basis_fade_entry": bool(rejection_entries),
            "all_entries_are_rejection_family": not acceptance_entries,
            "all_rejection_entries_have_causal_opposing_basis": not malformed_basis,
            "no_shock_entry": not shock_entries,
            "admitted_count_matches_entries": int(
                diagnostics.get("candidate18_v8_basis_fade_admitted", 0),
            )
            == len(rejection_entries),
            "local_twin_execution_active": int(
                diagnostics.get("candidate18_v7_local_twin_batches", 0),
            )
            > 0,
            "single_exit_family_per_trade": int(
                diagnostics.get("candidate18_v7_opposite_release_events", 0),
            )
            == 0,
        },
    )
    decision.update(
        {
            "schema": "candidate-18-v8-development-v1",
            "period": period,
            "checks": checks,
            "integrity_pass": all(checks.values()),
            "basis_fade_entries": len(rejection_entries),
            "acceptance_entries": len(acceptance_entries),
            "shock_entries": shock_entries,
            "malformed_basis_entries": malformed_basis,
            "metrics": metrics,
            "diagnostics": diagnostics,
        },
    )
    return decision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period", required=True)
    args = parser.parse_args()
    decision = validate(args.root.resolve(), args.period)
    destination = args.root.resolve() / "v8_development_decision.json"
    destination.write_text(
        json.dumps(decision, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, indent=2, sort_keys=True, allow_nan=False))
    if not decision["integrity_pass"]:
        raise SystemExit("Candidate 18 v8 integrity failure")


if __name__ == "__main__":
    main()
