from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import easychart_b_v4 as router
import fit_trigger_v4 as fitter


def _base_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "research_period": "p",
                "state_id": "s1",
                "episode_id": "e1",
                "action_id": "a1",
                "order_time_ns": 100,
                "entry_geometry": "ZONE_PROXIMAL_LIMIT",
                "family": "FAILED_AUCTION_REVERSAL",
                "auction_phase": "ACCEPTED_EXPANSION",
                "gross_rr": 2.5,
                "planned_target_net_r": 1.4,
                "entry": 100.0,
                "target": 102.5,
                "risk_bps": 100.0,
                "arm_progress_r": 0.30,
                "arm_structural_target_consumed_fraction": 0.12,
                "arm_structural_target_headroom_r": 2.20,
                "arm_outside_close_ratio": 0.70,
                "arm_outside_volume_share": 0.68,
                "arm_path_efficiency": 0.40,
                "arm_current_retrace_fraction": 0.25,
                "trigger_score": 0.80,
                "outcome": "UNFILLED",
                "order_terminal_time_ns": 110,
            },
            {
                "research_period": "p",
                "state_id": "s2",
                "episode_id": "e1",
                "action_id": "a2",
                "order_time_ns": 200,
                "entry_geometry": "ZONE_PROXIMAL_LIMIT",
                "family": "FAILED_AUCTION_REVERSAL",
                "auction_phase": "ACCEPTED_EXPANSION",
                "gross_rr": 2.5,
                "planned_target_net_r": 1.4,
                "entry": 100.0,
                "target": 102.5,
                "risk_bps": 100.0,
                "arm_progress_r": 0.70,
                "arm_structural_target_consumed_fraction": 0.28,
                "arm_structural_target_headroom_r": 1.80,
                "arm_outside_close_ratio": 0.90,
                "arm_outside_volume_share": 0.90,
                "arm_path_efficiency": 0.70,
                "arm_current_retrace_fraction": 0.15,
                "trigger_score": 0.96,
                "outcome": "UNFILLED",
                "order_terminal_time_ns": 210,
            },
        ]
    )


def test_runtime_features_exclude_identity_and_outcome() -> None:
    frame = _base_rows()
    frame["symbol"] = ["BTCUSDT", "XRPUSDT"]
    frame["net_r"] = [100.0, -100.0]
    features = router.raw_feature_frame(frame)
    assert list(features.columns) == router.FEATURE_NAMES
    forbidden = {"symbol", "research_period", "net_r", "outcome", "mfe_r"}
    assert forbidden.isdisjoint(features.columns)


def test_selects_earliest_sufficient_confirmation_not_latest_best_score() -> None:
    frame = _base_rows()
    policy = {
        "selection": {
            "minimum_net_completion_r": 0.25,
            "minimum_progress_r": 0.10,
            "minimum_consumed_fraction": 0.0,
            "maximum_consumed_fraction": 0.75,
            "minimum_headroom_r": 0.35,
            "maximum_current_retrace_fraction": 0.60,
            "minimum_acceptance_ratio": 0.40,
            "minimum_path_efficiency": 0.05,
            "score_threshold": 0.75,
            "allowed_phases": ["ACCEPTED_EXPANSION"],
        }
    }
    selected = router.select_plans(frame, policy, pre_scored=True)
    assert selected.action_id.tolist() == ["a1"]


def test_regularized_model_is_finite() -> None:
    rng = np.random.default_rng(7)
    frame = pd.concat([_base_rows()] * 30, ignore_index=True)
    frame["action_id"] = [f"a{i}" for i in range(len(frame))]
    frame["episode_id"] = [f"e{i // 2}" for i in range(len(frame))]
    frame["outcome"] = np.where(
        np.arange(len(frame)) % 3, "TARGET_FIRST", "STOP_FIRST"
    )
    frame["net_r"] = np.where(frame.outcome.eq("TARGET_FIRST"), 1.2, -1.0)
    frame["mfe_r"] = np.where(frame.outcome.eq("TARGET_FIRST"), 1.4, 0.3)
    frame["arm_path_efficiency"] = rng.uniform(0.05, 0.9, len(frame))
    model = fitter.fit_model(frame)
    assert len(model["coef_base_excursion"]) == len(router.FEATURE_NAMES) + 1
    assert np.isfinite(model["coef_base_excursion"]).all()
    assert np.isfinite(model["coef_structural_completion"]).all()


def test_no_target_cap_field_exists() -> None:
    source = Path(__file__).with_name("harvest_structural_v4.py").read_text(
        encoding="utf-8"
    )
    assert "MAX_TARGET" not in source
    assert "NEAREST_STILL_LIVE_CAUSAL_STRUCTURE_NO_FIXED_R_CAP" in source
