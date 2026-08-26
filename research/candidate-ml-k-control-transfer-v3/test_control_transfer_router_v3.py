from __future__ import annotations

import pandas as pd

from control_transfer_router_v3 import choose_public_plans, route_account, scenario_masks


def row(action: str, state: str, episode: str, time: int, outcome: str, *, terminal: int, resolution=None, net_r=None) -> dict:
    return {
        "action_id": action,
        "state_id": state,
        "episode_id": episode,
        "research_period": "p",
        "symbol": "BTCUSDT",
        "side": "LONG",
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
        "source_defense_count": 7.0,
        "sequence_block_0_return_bps_signed": 0.0,
        "sequence_block_5_delta_share_signed": 0.0,
        "event_impact_per_activity": 1.0,
        "risk_bps": 10.0,
        "sequence_block_3_return_bps_signed": 0.0,
        "approach_delta_share_12m_toward": 0.05,
        "departure_residual_return_5m_signed": 0.0012,
        "route_obstacle_distance_bps": 0.0,
        "structure_60m_high_change_atr": 0.0,
        "structure_60m_low_change_atr": 0.0,
    }


def test_passive_defended_residual_control_requires_all_three_parts() -> None:
    frame = pd.DataFrame([
        row("yes", "s1", "e1", 10, "TARGET_FIRST", terminal=20, resolution=20, net_r=2.0),
        {**row("no-defense", "s2", "e2", 30, "TARGET_FIRST", terminal=40, resolution=40, net_r=2.0), "source_defense_count": 6.0},
        {**row("no-control", "s3", "e3", 50, "TARGET_FIRST", terminal=60, resolution=60, net_r=2.0), "departure_residual_return_5m_signed": 0.0009},
    ])
    assert scenario_masks(frame)["PASSIVE_DEFENDED_RESIDUAL_CONTROL"].tolist() == [True, False, False]


def test_unfilled_order_occupies_the_single_global_slot() -> None:
    frame = pd.DataFrame([
        row("a", "s1", "e1", 10, "UNFILLED", terminal=30),
        row("b", "s2", "e2", 20, "TARGET_FIRST", terminal=25, resolution=25, net_r=2.0),
        row("c", "s3", "e3", 31, "STOP_FIRST", terminal=40, resolution=35, net_r=-1.0),
    ])
    orders, trades = route_account(choose_public_plans(frame))
    assert list(orders.action_id) == ["a", "c"]
    assert list(trades.net_r_num) == [-1.0]
