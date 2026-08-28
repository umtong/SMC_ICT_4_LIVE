from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier

import easychart_b_v6 as expert
import easychart_b_v7 as policy


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "action_id": ["a1", "a2"],
            "state_id": ["s1", "s2"],
            "episode_id": ["e1", "e2"],
            "order_time_ns": [100, 200],
            "entry_geometry": ["ZONE_PROXIMAL_LIMIT", "ZONE_PROXIMAL_LIMIT"],
            "family": ["FAILED_AUCTION_REVERSAL", "ACCEPTED_AUCTION_CONTINUATION"],
            "auction_phase": ["ACCEPTED_EXPANSION", "FIRST_RETEST_FORMING"],
            "location_kind": ["FVG_OB_OVERLAP", "BOUNDARY_FVG_OVERLAP"],
            "source_defense_count": [7.0, 6.0],
            "departure_residual_return_3m_signed": [0.0010, 0.0003],
            "departure_residual_return_5m_signed": [0.0010, 0.0002],
            "departure_common_return_30m_signed": [-0.0010, 0.0010],
            "arm_index_return_signed": [0.0001, 0.0020],
            "departure_basis_change_3m_signed": [-2.0, -1.2],
            "departure_impact_per_activity": [0.08, 0.10],
            "approach_delta_share_12m_toward": [0.03, 0.05],
            "sequence_block_0_return_bps_signed": [45.0, 35.0],
            "sequence_block_2_return_bps_signed": [1.0, 2.0],
            "sequence_block_5_delta_share_signed": [-0.20, -0.12],
            "zone_width_bps": [12.0, 9.0],
            "arm_outside_close_ratio": [0.75, 0.70],
            "arm_outside_volume_share": [0.72, 0.68],
            "arm_path_efficiency": [0.55, 0.50],
            "arm_activity_ratio": [1.2, 1.1],
            "arm_futures_index_residual_signed": [0.0010, 0.0003],
            "arm_structural_target_consumed_fraction": [0.20, 0.25],
            "arm_progress_r": [0.40, 0.45],
            "gross_rr": [2.2, 2.0],
            "planned_target_net_r": [1.25, 1.10],
            "risk_bps": [100.0, 100.0],
            "entry": [100.0, 100.0],
            "target": [102.2, 102.0],
            "arm_structural_target_headroom_r": [1.8, 1.55],
            "arm_current_retrace_fraction": [0.20, 0.20],
            "symbol": ["BTCUSDT", "XRPUSDT"],
            "research_period": ["p1", "p2"],
            "outcome": ["TARGET_FIRST", "STOP_FIRST"],
            "net_r": [8.0, -8.0],
            "mfe_r": [9.0, 0.1],
        }
    )


def _constant_bundle(feature_count: int) -> dict[str, DummyClassifier]:
    matrix = np.zeros((4, feature_count), dtype=float)
    bundle: dict[str, DummyClassifier] = {}
    for name, constant in (
        ("base_excursion", 1),
        ("structural_completion", 1),
        ("earliest_good_arm", 1),
    ):
        model = DummyClassifier(strategy="constant", constant=constant)
        model.fit(matrix, np.full(4, constant, dtype=int))
        bundle[name] = model
    return bundle


def test_runtime_features_exclude_identity_and_outcomes() -> None:
    features = policy.raw_feature_frame(_frame())
    assert {
        "symbol",
        "research_period",
        "outcome",
        "net_r",
        "mfe_r",
    }.isdisjoint(features.columns)


def test_nonlinear_heads_score_only_order_time_features() -> None:
    frame = _frame()
    feature_names = list(policy.raw_feature_frame(frame).columns)
    scored = policy.score_actions(
        frame,
        {
            "feature_names": feature_names,
            "score_weights": {"base": 0.46, "completion": 0.34, "early": 0.20},
        },
        _constant_bundle(len(feature_names)),
    )
    assert scored["trigger_score"].gt(0.99).all()
    assert scored["base_excursion_probability"].eq(1.0).all()


def test_expert_votes_cannot_choose_or_clip_target() -> None:
    source = Path(__file__).with_name("harvest_structural_v4.py").read_text(
        encoding="utf-8"
    )
    expert_source = Path(expert.__file__).read_text(encoding="utf-8")
    assert "MAX_TARGET" not in source
    assert "NEAREST_STILL_LIVE_CAUSAL_STRUCTURE_NO_FIXED_R_CAP" in source
    assert "expert_votes_choose_target\": False" in expert_source
