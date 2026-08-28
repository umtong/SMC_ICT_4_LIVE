from __future__ import annotations

from pathlib import Path

import pandas as pd

import easychart_b_v6 as policy


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "action_id": ["a1"],
            "family": ["FAILED_AUCTION_REVERSAL"],
            "auction_phase": ["ACCEPTED_EXPANSION"],
            "location_kind": ["FVG_OB_OVERLAP"],
            "source_defense_count": [7.0],
            "departure_residual_return_3m_signed": [0.001],
            "departure_residual_return_5m_signed": [0.001],
            "departure_common_return_30m_signed": [-0.001],
            "arm_index_return_signed": [0.0001],
            "departure_basis_change_3m_signed": [-2.0],
            "departure_impact_per_activity": [0.08],
            "approach_delta_share_12m_toward": [0.03],
            "sequence_block_0_return_bps_signed": [45.0],
            "sequence_block_5_delta_share_signed": [-0.20],
            "zone_width_bps": [12.0],
            "arm_outside_close_ratio": [0.75],
            "arm_outside_volume_share": [0.72],
            "arm_path_efficiency": [0.55],
            "arm_structural_target_consumed_fraction": [0.20],
            "arm_progress_r": [0.40],
            "gross_rr": [2.2],
            "planned_target_net_r": [1.25],
            "risk_bps": [100.0],
            "entry": [100.0],
            "target": [102.2],
            "arm_structural_target_headroom_r": [1.8],
            "arm_current_retrace_fraction": [0.2],
            "symbol": ["BTCUSDT"],
            "research_period": ["2025-apr"],
            "outcome": ["TARGET_FIRST"],
            "net_r": [9.0],
        }
    )


def test_expert_inputs_are_neutral_and_outcome_free() -> None:
    neutral = policy._neutral_frame(_frame())
    assert neutral.symbol.eq("NEUTRAL").all()
    assert neutral.research_period.eq("NEUTRAL").all()
    assert "outcome" not in neutral
    assert "net_r" not in neutral


def test_v5_causal_vote_remains_available() -> None:
    flags = policy.expert_flags(_frame())
    assert bool(flags.loc[0, "V5_CAUSAL_MECHANISM"])


def test_runtime_features_do_not_include_identity_or_outcomes() -> None:
    features = policy.raw_feature_frame(_frame())
    assert {
        "symbol",
        "research_period",
        "outcome",
        "net_r",
        "mfe_r",
    }.isdisjoint(features.columns)


def test_target_is_never_capped_by_r() -> None:
    source = Path(__file__).with_name("harvest_structural_v4.py").read_text(
        encoding="utf-8"
    )
    assert "MAX_TARGET" not in source
    assert "NEAREST_STILL_LIVE_CAUSAL_STRUCTURE_NO_FIXED_R_CAP" in source
