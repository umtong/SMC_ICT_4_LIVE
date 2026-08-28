from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import easychart_b_v5 as policy


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "family": ["FAILED_AUCTION_REVERSAL", "ACCEPTED_AUCTION_CONTINUATION"],
            "auction_phase": ["ACCEPTED_EXPANSION", "FIRST_RETEST_FORMING"],
            "location_kind": ["FVG_OB_OVERLAP", "BOUNDARY_FVG_OVERLAP"],
            "source_defense_count": [7.0, 6.0],
            "departure_residual_return_3m_signed": [0.001, 0.0003],
            "departure_residual_return_5m_signed": [0.001, 0.0002],
            "departure_common_return_30m_signed": [-0.001, 0.001],
            "arm_index_return_signed": [0.0001, 0.002],
            "departure_basis_change_3m_signed": [-2.0, -1.2],
            "departure_impact_per_activity": [0.08, 0.10],
            "approach_delta_share_12m_toward": [0.03, 0.05],
            "sequence_block_0_return_bps_signed": [45.0, 35.0],
            "sequence_block_5_delta_share_signed": [-0.20, -0.12],
            "zone_width_bps": [12.0, 9.0],
            "arm_outside_close_ratio": [0.75, 0.70],
            "arm_outside_volume_share": [0.72, 0.68],
            "arm_path_efficiency": [0.55, 0.50],
            "arm_structural_target_consumed_fraction": [0.20, 0.25],
            "arm_progress_r": [0.40, 0.45],
            "gross_rr": [2.2, 2.0],
            "planned_target_net_r": [1.25, 1.10],
            "risk_bps": [100.0, 100.0],
            "entry": [100.0, 100.0],
            "target": [102.2, 102.0],
            "arm_structural_target_headroom_r": [1.8, 1.55],
            "arm_current_retrace_fraction": [0.2, 0.2],
        }
    )


def test_runtime_features_exclude_identity_and_outcomes() -> None:
    frame = _frame()
    frame["symbol"] = ["BTCUSDT", "XRPUSDT"]
    frame["research_period"] = ["a", "b"]
    frame["outcome"] = ["TARGET_FIRST", "STOP_FIRST"]
    frame["net_r"] = [10.0, -10.0]
    features = policy.raw_feature_frame(frame)
    assert {"symbol", "research_period", "outcome", "net_r", "mfe_r"}.isdisjoint(features.columns)


def test_trade_requires_observable_causal_mechanism() -> None:
    frame = _frame()
    assert pd.concat(policy.mechanism_masks(frame), axis=1).any(axis=1).all()
    broken = frame.copy()
    for column in (
        "source_defense_count",
        "departure_residual_return_3m_signed",
        "departure_residual_return_5m_signed",
        "departure_basis_change_3m_signed",
        "sequence_block_0_return_bps_signed",
        "sequence_block_5_delta_share_signed",
        "arm_index_return_signed",
    ):
        broken[column] = np.nan
    assert not pd.concat(policy.mechanism_masks(broken), axis=1).any(axis=1).all()


def test_structural_harvester_has_no_fixed_r_target_cap() -> None:
    source = Path(__file__).with_name("harvest_structural_v4.py").read_text(encoding="utf-8")
    assert "MAX_TARGET" not in source
    assert "NEAREST_STILL_LIVE_CAUSAL_STRUCTURE_NO_FIXED_R_CAP" in source
