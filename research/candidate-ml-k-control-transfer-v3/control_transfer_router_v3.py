#!/usr/bin/env python3
"""Frozen Candidate ML-k V3: V1 core plus two independent opportunity families."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
V2_DIR = HERE.parent / "candidate-ml-k-control-transfer-v2"
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))
import control_transfer_router_v2 as core  # noqa: E402

THRESHOLDS = {
    key: value
    for key, value in core.THRESHOLDS.items()
    if key not in {
        "opposing_flow_approach_delta",
        "opposing_flow_source_defenses",
        "accumulated_source_minutes",
        "thin_route_low_volume_fraction",
    }
}
THRESHOLDS.update({
    "passive_defended_approach_delta": 0.08,
    "passive_defended_source_defenses": 7.0,
    "residual_control_return": 0.001,
})

route_account = core.route_account
load_actions = core.load_actions

SCENARIO_PRIORITY = {
    "DEFENDED_BASIS_ABSORPTION": 6,
    "PUSH_PULL_ABSORPTION": 5,
    "EVENT_ABSORPTION_DISPLACEMENT": 4,
    "EFFICIENT_APPROACH_SOURCE": 3,
    "PASSIVE_DEFENDED_RESIDUAL_CONTROL": 2,
    "PASSIVE_APPROACH_OPEN_ROUTE_STRUCTURE_EXPANSION": 1,
}


def scenario_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    t = THRESHOLDS
    structure_extension = pd.Series(
        np.where(
            frame.get("side", pd.Series("", index=frame.index)).astype(str).eq("LONG"),
            core._num(frame, "structure_60m_high_change_atr"),
            -core._num(frame, "structure_60m_low_change_atr"),
        ),
        index=frame.index,
    )
    return {
        "EFFICIENT_APPROACH_SOURCE": (
            core._num(frame, "approach_impact_per_activity_12m").ge(t["efficient_approach_impact"])
            & core._num(frame, "approach_path_efficiency").ge(t["efficient_approach_path"])
            & core._num(frame, "zone_width_bps").ge(t["meaningful_zone_bps"])
        ),
        "DEFENDED_BASIS_ABSORPTION": (
            core._num(frame, "departure_basis_change_3m_signed").le(t["basis_compression_bps"])
            & core._num(frame, "departure_impact_per_activity").le(t["departure_low_impact"])
            & core._num(frame, "source_defense_count").ge(t["source_defenses"])
        ),
        "PUSH_PULL_ABSORPTION": (
            core._num(frame, "sequence_block_0_return_bps_signed").ge(t["initial_displacement_bps"])
            & core._num(frame, "sequence_block_5_delta_share_signed").le(t["late_opposite_delta"])
            & core._num(frame, "zone_width_bps").ge(t["wide_zone_bps"])
        ),
        "EVENT_ABSORPTION_DISPLACEMENT": (
            core._num(frame, "event_impact_per_activity").le(t["event_absorption_impact"])
            & core._num(frame, "risk_bps").ge(t["event_absorption_risk_bps"])
            & core._num(frame, "sequence_block_3_return_bps_signed").ge(t["late_displacement_bps"])
        ),
        "PASSIVE_DEFENDED_RESIDUAL_CONTROL": (
            core._num(frame, "approach_delta_share_12m_toward").le(t["passive_defended_approach_delta"])
            & core._num(frame, "source_defense_count").ge(t["passive_defended_source_defenses"])
            & core._num(frame, "departure_residual_return_5m_signed").ge(t["residual_control_return"])
        ),
        "PASSIVE_APPROACH_OPEN_ROUTE_STRUCTURE_EXPANSION": (
            core._num(frame, "approach_delta_share_12m_toward").le(t["passive_approach_delta"])
            & core._num(frame, "route_obstacle_distance_bps").ge(t["open_route_obstacle_bps"])
            & structure_extension.ge(t["structure_extension_atr"])
        ),
    }


def label_scenarios(frame: pd.DataFrame) -> pd.DataFrame:
    masks = scenario_masks(frame)
    selected = frame.loc[pd.concat(masks, axis=1).any(axis=1)].copy()
    selected["scenario_family"] = ""
    for name, _priority in sorted(SCENARIO_PRIORITY.items(), key=lambda item: item[1]):
        selected.loc[masks[name].reindex(selected.index, fill_value=False), "scenario_family"] = name
    selected["scenario_priority"] = selected["scenario_family"].map(SCENARIO_PRIORITY).astype(int)
    return selected


def choose_public_plans(frame: pd.DataFrame) -> pd.DataFrame:
    selected = label_scenarios(frame)
    target_r = core._num(selected, "planned_target_net_r")
    selected = selected[target_r.between(core.MIN_TARGET_NET_R, core.MAX_TARGET_NET_R)].copy()
    geometry = selected["entry_geometry"].astype(str) if "entry_geometry" in selected else pd.Series("", index=selected.index)
    selected["preferred_geometry"] = geometry.eq(core.GEOMETRY).astype(int)
    selected = selected.sort_values(
        ["state_id", "preferred_geometry", "planned_target_net_r", "action_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).drop_duplicates("state_id", keep="first")
    return selected.sort_values(
        ["order_time_ns", "scenario_priority", "planned_target_net_r", "action_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)


def run(root: Path, output: Path) -> dict:
    actions = core.load_actions(root)
    plans = choose_public_plans(actions)
    orders, trades = core.route_account(plans)
    original_priority = core.SCENARIO_PRIORITY
    try:
        core.SCENARIO_PRIORITY = SCENARIO_PRIORITY
        summary = core.summarize(actions, orders, trades)
    finally:
        core.SCENARIO_PRIORITY = original_priority
    summary["policy"] = "ML_K_CAUSAL_CONTROL_TRANSFER_V3"
    summary["scenario_thresholds"] = THRESHOLDS
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
