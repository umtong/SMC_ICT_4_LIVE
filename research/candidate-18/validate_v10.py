#!/usr/bin/env python3
"""Validate Candidate 18 v10 causality, stop geometry, and v7 execution."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from validate_v6 import validate as validate_v6


def _safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


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
    with (root / "orders.csv").open(newline="", encoding="utf-8") as stream:
        orders = list(csv.DictReader(stream))

    entries = [
        row
        for row in orders
        if "CANDIDATE18_BOUNDED_GTD_ENTRY" in str(row.get("tags", ""))
    ]
    local_stops = [
        row
        for row in orders
        if "CANDIDATE18_V7_LOCAL_TWIN_STOP" in str(row.get("tags", ""))
    ]
    local_targets = [
        row
        for row in orders
        if "CANDIDATE18_V7_LOCAL_TWIN_TARGET" in str(row.get("tags", ""))
    ]
    submissions = [
        event for event in events if event.get("event_type") == "ENTRY_SUBMITTED"
    ]
    relaunch_events = [
        event
        for event in events
        if event.get("event_type") == "SECOND_LEG_RELAUNCH_CONFIRMED"
    ]

    malformed_routes: list[str | None] = []
    malformed_stop_anchors: list[str | None] = []
    for event in submissions:
        details = event.get("details", {})
        scenario_id = event.get("scenario_id")
        try:
            opening_time_ns = int(details["opening_time_ns"])
            retest_time_ns = int(details["retest_time_ns"])
            relaunch_time_ns = int(details["relaunch_time_ns"])
            event_time_ns = int(event["event_time_ns"])
            side = int(details["side"])
            accepted = float(details["boundary"])
            opposite = float(details["opposite_boundary"])
            stop = float(details["stop"])
            launch_level = float(details["relaunch_level"])
            signal_close = float(details["signal_close"])
        except (KeyError, TypeError, ValueError):
            malformed_routes.append(scenario_id)
            continue
        if (
            details.get("branch") != "QUARTER_HOUR_RELAUNCH"
            or details.get("scenario_family")
            != "QUARTER_HOUR_SECOND_LEG_RELAUNCH"
            or details.get("stop_anchor")
            != "OPPOSITE_PRE_EVENT_RANGE_BOUNDARY"
            or side not in (-1, 1)
            or not (
                0 < opening_time_ns < retest_time_ns < relaunch_time_ns
                == event_time_ns
            )
            or not all(
                math.isfinite(value)
                for value in (
                    accepted,
                    opposite,
                    stop,
                    launch_level,
                    signal_close,
                )
            )
            or (side > 0 and signal_close <= launch_level)
            or (side < 0 and signal_close >= launch_level)
        ):
            malformed_routes.append(scenario_id)
        stop_is_structural = (
            side > 0 and stop < opposite < accepted < signal_close
        ) or (
            side < 0 and stop > opposite > accepted > signal_close
        )
        if not stop_is_structural:
            malformed_stop_anchors.append(scenario_id)

    relaunch_by_scenario = {
        event.get("scenario_id") for event in relaunch_events
    }
    missing_relaunch_event = [
        event.get("scenario_id")
        for event in submissions
        if event.get("scenario_id") not in relaunch_by_scenario
    ]

    filled_qty = float(decision.get("filled_entry_qty", 0.0))
    stop_qty = float(diagnostics.get("candidate18_v7_local_stop_qty", 0.0))
    target_qty = float(
        diagnostics.get("candidate18_v7_local_target_qty", 0.0),
    )
    has_entries = bool(entries)
    checks = dict(decision["checks"])
    if not has_entries:
        for key in (
            "bounded_gtd_entry_contract",
            "has_filled_trade",
            "fill_quantity_matches_diagnostics",
            "stop_capacity_covers_fills",
            "target_capacity_covers_fills",
            "protection_created",
            "positions_match_signals",
        ):
            checks[key] = True
    checks.update(
        {
            "v10_candidate_recorded": metrics.get("candidate")
            == "candidate-18-v10-quarter-hour-relaunch",
            "independent_family_recorded": metrics.get("independent_family")
            is True,
            "only_relaunch_family_enters": not malformed_routes,
            "opening_retest_relaunch_are_strictly_ordered": not malformed_routes,
            "opposite_boundary_is_stop_anchor": not malformed_stop_anchors,
            "every_entry_has_relaunch_transition": not missing_relaunch_event,
            "context_count_is_coherent": int(
                diagnostics.get("candidate18_v10_contexts_armed", 0),
            )
            >= int(
                diagnostics.get("candidate18_v10_retests_armed", 0),
            )
            >= int(
                diagnostics.get(
                    "candidate18_v10_relaunch_confirmations",
                    0,
                ),
            ),
            "entry_count_not_above_relaunch_confirmations": len(entries)
            <= int(
                diagnostics.get(
                    "candidate18_v10_relaunch_confirmations",
                    0,
                ),
            ),
            "local_twin_batches_created_when_filled": (
                filled_qty <= 1e-12
                or int(
                    diagnostics.get(
                        "candidate18_v7_local_twin_batches",
                        0,
                    ),
                )
                > 0
            ),
            "local_stop_capacity_covers_fills": (
                filled_qty <= 1e-12 or stop_qty + 1e-8 >= filled_qty
            ),
            "local_target_capacity_covers_fills": (
                filled_qty <= 1e-12 or target_qty + 1e-8 >= filled_qty
            ),
            "local_stop_orders_observed_when_filled": (
                filled_qty <= 1e-12 or bool(local_stops)
            ),
            "local_target_contract_observed_when_filled": (
                filled_qty <= 1e-12 or bool(local_targets)
            ),
            "target_release_is_market": all(
                row.get("type") == "MARKET" for row in local_targets
            ),
            "both_exits_reduce_only": all(
                str(row.get("is_reduce_only")) == "True"
                for row in [*local_stops, *local_targets]
            ),
            "single_exit_family_per_trade": int(
                diagnostics.get("candidate18_v7_opposite_release_events", 0),
            )
            == 0,
            "no_late_reduce_only_race": not list(
                diagnostics.get(
                    "candidate18_v6_order_rejection_events",
                    [],
                ),
            ),
        },
    )
    decision.update(
        {
            "schema": "candidate-18-v10-development-v1",
            "period": period,
            "checks": checks,
            "integrity_pass": all(checks.values()),
            "malformed_routes": malformed_routes,
            "malformed_stop_anchors": malformed_stop_anchors,
            "missing_relaunch_event": missing_relaunch_event,
            "contexts_armed": int(
                diagnostics.get("candidate18_v10_contexts_armed", 0),
            ),
            "retests_armed": int(
                diagnostics.get("candidate18_v10_retests_armed", 0),
            ),
            "relaunch_confirmations": int(
                diagnostics.get(
                    "candidate18_v10_relaunch_confirmations",
                    0,
                ),
            ),
            "entry_rows": len(entries),
            "local_stop_rows": len(local_stops),
            "local_target_rows": len(local_targets),
            "local_stop_qty": stop_qty,
            "local_target_qty": target_qty,
            "metrics": metrics,
            "diagnostics": diagnostics,
        },
    )
    return _safe(decision)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period", required=True)
    args = parser.parse_args()
    decision = validate(args.root.resolve(), args.period)
    destination = args.root.resolve() / "v10_development_decision.json"
    destination.write_text(
        json.dumps(decision, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, indent=2, sort_keys=True, allow_nan=False))
    if not decision["integrity_pass"]:
        raise SystemExit("Candidate 18 v10 integrity failed")


if __name__ == "__main__":
    main()
