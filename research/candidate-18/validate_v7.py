#!/usr/bin/env python3
"""Validate Candidate 18 v7 local twin-trigger execution."""
from __future__ import annotations

import argparse
import csv
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
    with (root / "orders.csv").open(newline="", encoding="utf-8") as stream:
        orders = list(csv.DictReader(stream))

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
    filled_qty = float(decision.get("filled_entry_qty", 0.0))
    stop_qty = float(diagnostics.get("candidate18_v7_local_stop_qty", 0.0))
    target_qty = float(
        diagnostics.get("candidate18_v7_local_target_qty", 0.0),
    )
    checks = dict(decision["checks"])
    checks.update(
        {
            "v7_candidate_recorded": metrics.get("candidate")
            == "candidate-18-v7-local-twin-trigger-router",
            "local_twin_batches_created": int(
                diagnostics.get("candidate18_v7_local_twin_batches", 0),
            )
            > 0,
            "local_stop_capacity_covers_fills": stop_qty + 1e-8
            >= filled_qty,
            "local_target_capacity_covers_fills": target_qty + 1e-8
            >= filled_qty,
            "local_stop_orders_observed": bool(local_stops),
            "local_target_orders_observed": bool(local_targets),
            "target_is_market_if_touched": all(
                row.get("type") == "MARKET_IF_TOUCHED"
                for row in local_targets
            ),
            "both_exits_reduce_only": all(
                str(row.get("is_reduce_only")) == "True"
                for row in [*local_stops, *local_targets]
            ),
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
            "schema": "candidate-18-v7-development-v1",
            "period": period,
            "checks": checks,
            "integrity_pass": all(checks.values()),
            "local_stop_rows": len(local_stops),
            "local_target_rows": len(local_targets),
            "local_stop_qty": stop_qty,
            "local_target_qty": target_qty,
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
    destination = args.root.resolve() / "v7_development_decision.json"
    destination.write_text(
        json.dumps(decision, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, indent=2, sort_keys=True, allow_nan=False))
    if not decision["integrity_pass"]:
        raise SystemExit("Candidate 18 v7 execution integrity failed")


if __name__ == "__main__":
    main()
