from __future__ import annotations

import pandas as pd

import easychart_b as policy


def base_row(**updates):
    row = {
        "family": "FAILED_AUCTION_REVERSAL",
        "auction_phase": "ACCEPTED_EXPANSION",
        "location_kind": "BPR",
        "target_scale_minutes": 1440,
        "departure_common_return_30m_signed": -0.0001,
        "departure_residual_return_3m_signed": 0.0001,
        "departure_basis_change_3m_signed": 0.0,
        "departure_impact_per_activity": 1.0,
        "source_defense_count": 1,
        "sequence_block_0_return_bps_signed": 0.0,
        "sequence_block_5_delta_share_signed": 0.0,
        "zone_width_bps": 10.0,
        "approach_delta_share_12m_toward": 1.0,
        "departure_residual_return_5m_signed": 0.0,
        "planned_target_net_r": 1.4,
        "entry_geometry": "ZONE_PROXIMAL_LIMIT",
        "state_id": "s1",
        "episode_id": "e1",
        "research_period": "p1",
        "order_time_ns": 1_000,
        "action_id": "a1",
        "symbol": "BTCUSDT",
        "outcome": "STOP_FIRST",
        "net_r": -1.0,
        "resolution_time_ns": 2_000,
        "order_terminal_time_ns": 2_000,
    }
    row.update(updates)
    return row


def test_relative_control_selection_ignores_symbol_and_outcome():
    original = pd.DataFrame([base_row()])
    changed = original.copy()
    changed.loc[0, "symbol"] = "XRPUSDT"
    changed.loc[0, "outcome"] = "TARGET_FIRST"
    changed.loc[0, "net_r"] = 1.4
    left = policy.select_plans(original)
    right = policy.select_plans(changed)
    assert left[["state_id", "action_id", "scenario_family"]].to_dict("records") == right[
        ["state_id", "action_id", "scenario_family"]
    ].to_dict("records")
    assert left.iloc[0]["scenario_family"] == "RELATIVE_CONTROL_TRANSFER"


def test_late_retest_does_not_replace_failed_relative_transfer():
    frame = pd.DataFrame(
        [
            base_row(
                auction_phase="FIRST_RETEST_FORMING",
                state_id="s2",
                action_id="a2",
            )
        ]
    )
    assert policy.select_plans(frame).empty


def test_defended_source_requires_extra_evidence_for_deep_retest():
    weak = pd.DataFrame(
        [
            base_row(
                family="ACCEPTED_AUCTION_CONTINUATION",
                auction_phase="DEEP_RETEST",
                location_kind="TRANSFERRED_BOUNDARY",
                departure_common_return_30m_signed=1.0,
                departure_residual_return_3m_signed=-1.0,
                departure_basis_change_3m_signed=-2.0,
                departure_impact_per_activity=0.05,
                source_defense_count=6,
                state_id="s3",
                action_id="a3",
            )
        ]
    )
    strong = weak.copy()
    strong.loc[0, "source_defense_count"] = 7
    assert policy.select_plans(weak).empty
    selected = policy.select_plans(strong)
    assert selected.iloc[0]["scenario_family"] == "DEFENDED_SOURCE_ABSORPTION"


def test_one_episode_and_one_global_slot():
    rows = [
        base_row(
            state_id="s1",
            episode_id="e1",
            order_time_ns=1_000,
            action_id="a1",
            outcome="TARGET_FIRST",
            net_r=1.4,
            resolution_time_ns=3_000,
        ),
        base_row(
            state_id="s2",
            episode_id="e1",
            order_time_ns=1_500,
            action_id="a2",
            outcome="TARGET_FIRST",
            net_r=1.4,
            resolution_time_ns=2_500,
        ),
        base_row(
            state_id="s3",
            episode_id="e2",
            order_time_ns=2_000,
            action_id="a3",
            outcome="TARGET_FIRST",
            net_r=1.4,
            resolution_time_ns=4_000,
        ),
        base_row(
            state_id="s4",
            episode_id="e3",
            order_time_ns=4_000,
            action_id="a4",
            outcome="TARGET_FIRST",
            net_r=1.4,
            resolution_time_ns=5_000,
        ),
    ]
    plans = policy.select_plans(pd.DataFrame(rows))
    orders, trades = policy.route_continuous_account(plans)
    assert list(trades["episode_id"]) == ["e1", "e3"]
    assert len(orders) == 2
