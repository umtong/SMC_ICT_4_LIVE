#!/usr/bin/env python3
"""Run V13 with explicit archive gaps and collision-free row accounting.

Candidate 13 scenario identifiers are local to a session/range and can repeat in
different development weeks. Counterfactual accounting therefore must retain or
reject each trade row directly, rather than using scenario_id as a global key.
"""
from __future__ import annotations

import sys
from typing import Any

import oi_inventory_state_diagnostics as diagnostic
import oi_inventory_state_diagnostics_v2 as gap_runner


def _policy_counterfactual(records: list[dict[str, Any]]) -> dict[str, Any]:
    retained = [
        row for row in records
        if row["scenario"] != "FAR" or bool(row["reset_compatible_c05"])
    ]
    rejected = [
        row for row in records
        if row["scenario"] == "FAR" and not bool(row["reset_compatible_c05"])
    ]
    if len(retained) + len(rejected) != len(records):
        raise RuntimeError(
            "row accounting mismatch: "
            f"retained={len(retained)} rejected={len(rejected)} total={len(records)}",
        )

    def row_key(row: dict[str, Any]) -> str:
        return (
            f"W{int(row['week']):02d}|{row['scenario_id']}|"
            f"{int(row['confirmation_ts_ns'])}"
        )

    pnls = [float(row["pnl"]) for row in retained]
    return {
        "name": "CANDIDATE05_POSITIONING_RESET_COMPATIBLE_FAR",
        "rule": (
            "retain AAC; retain FAR only when a post-sweep causal metrics row is "
            "available and confirmation oi_change_15m <= 0.001"
        ),
        "retained_trades": len(retained),
        "rejected_trades": len(rejected),
        "retained_wins": sum(float(row["pnl"]) > 0.0 for row in retained),
        "retained_losses": sum(float(row["pnl"]) < 0.0 for row in retained),
        "retained_net_pnl_usdt": sum(pnls),
        "rejected_net_pnl_usdt": sum(float(row["pnl"]) for row in rejected),
        "retained_row_keys": [row_key(row) for row in retained],
        "rejected_row_keys": [row_key(row) for row in rejected],
        "retained_scenario_ids": [row["scenario_id"] for row in retained],
        "rejected_scenario_ids": [row["scenario_id"] for row in rejected],
        "by_scenario": diagnostic.summarize(retained, "scenario"),
    }


diagnostic.policy_counterfactual = _policy_counterfactual


if __name__ == "__main__":
    status = diagnostic.main()
    gap_runner._augment_outputs()
    raise SystemExit(status)
