from __future__ import annotations

import pandas as pd

from control_transfer_router import choose_public_plans, route_account


def row(action: str, state: str, episode: str, time: int, outcome: str, *, terminal: int, resolution=None, net_r=None, geometry="ZONE_PROXIMAL_LIMIT") -> dict:
    return {
        "action_id": action,
        "state_id": state,
        "episode_id": episode,
        "research_period": "p",
        "symbol": "BTCUSDT",
        "order_time_ns": time,
        "entry_geometry": geometry,
        "planned_target_net_r": 2.0,
        "outcome": outcome,
        "order_terminal_time_ns": terminal,
        "resolution_time_ns": resolution,
        "net_r": net_r,
        "approach_impact_per_activity_12m": 1.0,
        "approach_path_efficiency": 0.5,
        "zone_width_bps": 9.0,
        "departure_basis_change_3m_signed": 0.0,
        "departure_impact_per_activity": 1.0,
        "source_defense_count": 0,
        "sequence_block_0_return_bps_signed": 0.0,
        "sequence_block_5_delta_share_signed": 0.0,
        "event_impact_per_activity": 1.0,
        "risk_bps": 10.0,
        "sequence_block_3_return_bps_signed": 0.0,
    }


def test_one_episode_and_one_global_slot() -> None:
    frame = pd.DataFrame([
        row("a-prox", "s1", "e1", 10, "TARGET_FIRST", terminal=20, resolution=30, net_r=2.0),
        row("a-mid", "s1", "e1", 10, "TARGET_FIRST", terminal=20, resolution=25, net_r=3.0, geometry="ZONE_MID_LIMIT"),
        row("b", "s2", "e2", 15, "TARGET_FIRST", terminal=22, resolution=24, net_r=2.0),
        row("c", "s3", "e3", 31, "STOP_FIRST", terminal=40, resolution=35, net_r=-1.0),
    ])
    orders, trades = route_account(choose_public_plans(frame))
    assert list(orders.action_id) == ["a-prox", "c"]
    assert list(trades.net_r_num) == [2.0, -1.0]
    assert abs(float(trades.iloc[-1].nav_after) - 1.06 * 0.97) < 1e-12


def test_unfilled_order_blocks_until_terminal() -> None:
    frame = pd.DataFrame([
        row("a", "s1", "e1", 10, "UNFILLED", terminal=30),
        row("b", "s2", "e2", 20, "TARGET_FIRST", terminal=25, resolution=26, net_r=2.0),
        row("c", "s3", "e3", 31, "TARGET_FIRST", terminal=40, resolution=35, net_r=2.0),
    ])
    orders, trades = route_account(choose_public_plans(frame))
    assert list(orders.action_id) == ["a", "c"]
    assert list(trades.action_id) == ["c"]
