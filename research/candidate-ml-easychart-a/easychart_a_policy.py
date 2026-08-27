#!/usr/bin/env python3
"""EasyChart-A: one-account causal liquidity-control day-trading policy.

The policy reuses the strongest causal-control-transfer mechanism already present
in the repository instead of rebuilding auction accounting. Its public decision
unit is a complete source -> response -> invalidation -> reachable frontier plan.
Selection never sees symbol identity, calendar fields, or realized outcomes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
V3_DIR = HERE.parent / "candidate-ml-k-control-transfer-v3"
V2_DIR = HERE.parent / "candidate-ml-k-control-transfer-v2"
for directory in (V3_DIR, V2_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import control_transfer_router_v3 as control  # noqa: E402

POLICY = "EASYCHART_A_CAUSAL_LIQUIDITY_CONTROL_V1"
RISK_FRACTION = control.core.RISK_FRACTION

# These are decision meanings, not extra gates. They preserve the shared
# EasyChart/SMC causal vocabulary while the underlying public features remain
# continuous market/flow measurements.
DECISION_MEANINGS = {
    "DEFENDED_BASIS_ABSORPTION": "defended source absorbs a compressed approach",
    "PUSH_PULL_ABSORPTION": "displacement is met by opposite late flow without losing control",
    "EVENT_ABSORPTION_DISPLACEMENT": "low-impact absorption precedes renewed displacement",
    "EFFICIENT_APPROACH_SOURCE": "efficient return reaches a meaningful inherited source",
    "PASSIVE_DEFENDED_RESIDUAL_CONTROL": "passive liquidity sweep/reclaim retains residual control",
    "PASSIVE_APPROACH_OPEN_ROUTE_STRUCTURE_EXPANSION": "accepted control transfer has an open reachable frontier",
}


def choose_public_plans(actions: pd.DataFrame) -> pd.DataFrame:
    """Choose one deterministic plan per state using only order-time fields."""
    plans = control.choose_public_plans(actions).copy()
    if not plans.empty:
        plans["decision_meaning"] = plans["scenario_family"].map(DECISION_MEANINGS).fillna("")
        plans["policy"] = POLICY
    return plans


def run(root: Path, output: Path) -> dict:
    actions = control.load_actions(root)
    plans = choose_public_plans(actions)
    orders, trades = control.route_account(plans)

    original_priority = control.core.SCENARIO_PRIORITY
    try:
        control.core.SCENARIO_PRIORITY = control.SCENARIO_PRIORITY
        summary = control.core.summarize(actions, orders, trades)
    finally:
        control.core.SCENARIO_PRIORITY = original_priority

    summary.update(
        {
            "policy": POLICY,
            "core_logic_common_across_symbols": True,
            "decision_uses_symbol_identity": False,
            "decision_uses_calendar_fields": False,
            "decision_uses_outcome_fields": False,
            "decision_sequence": [
                "meaningful inherited source or structural boundary",
                "approach/sweep/absorption state",
                "observable control transfer or residual control",
                "first executable source response",
                "structural invalidation",
                "nearest reachable completion frontier",
                "one global account-slot arbitration",
            ],
            "decision_meanings": DECISION_MEANINGS,
            "scenario_thresholds": control.THRESHOLDS,
        }
    )

    output.mkdir(parents=True, exist_ok=True)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.output)


if __name__ == "__main__":
    main()
