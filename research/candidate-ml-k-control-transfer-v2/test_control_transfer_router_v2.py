from __future__ import annotations

import pandas as pd

from control_transfer_router_v2 import scenario_masks, choose_public_plans, route_account


def row(action: str, state: str, episode: str, time: int, outcome: str, *, terminal: int, resolution=None, net_r=None, side="LONG", high_change=9.0, low_change=0.0) -> dict:
    return {
        "action_id": action,
        "state_id": state,
        "episode_id": episode,
        "research_period": "p",
        "symbol": "BTCUSDT",
        "side": side,
        "order_time_ns": time,
        "entry_geometry": "ZONE_PROXIMAL_LIMIT",
        "planned_target_net_r": 2.0,
        "outcome": outcome,
        "order_terminal_time_ns": terminal,
        "resolution_time_ns": resolution,
        "net_r": net_r,
        "approach_impact_per_activity_12m": 0.0,
        "approach_path_efficiency": 0.0,
        "zone_width_bps": 4.0,
        "departure_basis_change_3m_signed": 0.0,
        "departure_impact_per_activity": 1.0,
        "source_defense_count": 0.0,
        "sequence_block_0_return_bps_signed": 0.0,
        "sequence_block_5_delta_share_signed": 0.0,
        "event_impact_per_activity": 1.0,
        "risk_bps": 10.0,
        "sequence_block_3_return_bps_signed": 0.0,
        "source_accumulation_minutes_near": 0.0,
        "route_profile_path_low_volume_fraction": 0.0,
        "approach_delta_share_12m_toward": 0.0,
        "route_obstacle_distance_bps": 100.0,
        "structure_60m_high_change_atr": high_change,
        "structure_60m_low_change_atr": low_change,
    }


def test_structure_expansion_is_direction_symmetric() -> None:
    frame = pd.DataFrame([
        row("long", "s1", "e1", 10, "TARGET_FIRST", terminal=20, resolution=20, net_r=2.0, side="LONG", high_change=9.0),
        row("short", "s2", "e2", 30, "TARGET_FIRST", terminal=40, resolution=40, net_r=2.0, side="SHORT", high_change=0.0, low_change=-9.0),
    ])
    mask = scenario_masks(frame)["PASSIVE_APPROACH_OPEN_ROUTE_STRUCTURE_EXPANSION"]
    assert mask.tolist() == [True, True]


def test_one_global_slot_remains_causal() -> None:
    frame = pd.DataFrame([
        row("a", "s1", "e1", 10, "UNFILLED", terminal=30),
        row("b", "s2", "e2", 20, "TARGET_FIRST", terminal=25, resolution=25, net_r=2.0),
        row("c", "s3", "e3", 31, "STOP_FIRST", terminal=40, resolution=35, net_r=-1.0),
    ])
    orders, trades = route_account(choose_public_plans(frame))
    assert list(orders.action_id) == ["a", "c"]
    assert list(trades.net_r_num) == [-1.0]
