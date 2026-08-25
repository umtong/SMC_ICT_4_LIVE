from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd

import counterfactual_destination_router as router
import structural_destination_policy as policy
from world_model_common import Destination


def _destination(identifier: str, price: float, kind: str, scale: float) -> Destination:
    return Destination(
        destination_id=identifier,
        side="HIGH",
        lower=price,
        upper=price,
        price=price,
        observed_index=1,
        scale=scale,
        strength=1.0,
        kind=kind,
    )


def test_structural_frontiers_keep_first_obstacle_per_mechanism_scale() -> None:
    data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0],
        },
        index=pd.date_range("2025-01-01", periods=3, freq="min", tz="UTC"),
    )
    semantic_near = _destination("SEM-NEAR", 110.0, "SEMANTIC:SWING", 60.0)
    semantic_far = _destination("SEM-FAR", 120.0, "SEMANTIC:SWING", 60.0)
    dc_frontier = _destination(
        "DC", 130.0, "DIRECTIONAL_CHANGE_1.50", 90.0
    )
    with (
        patch.object(
            policy.liquidity_world,
            "_semantic_destinations",
            return_value=[semantic_near, semantic_far],
        ),
        patch.object(
            policy.liquidity_world,
            "_dc_destinations",
            return_value=[dc_frontier],
        ),
        patch.object(policy, "_dynamic_destinations", return_value=[]),
        patch.object(
            policy.liquidity_world,
            "_previous_day",
            return_value=None,
        ),
        patch.object(
            policy.liquidity_world,
            "_cluster",
            side_effect=lambda items, decision, atr, tick: list(items),
        ),
    ):
        frontiers = policy.structural_frontiers(
            symbol="BTCUSDT",
            data=data,
            levels=[],
            metadata={},
            nodes_by_scale={},
            decision=2,
            entry=100.0,
            side="LONG",
            atr=np.ones(3),
            tick=0.1,
        )
    identifiers = [item.destination_id for item in frontiers]
    assert identifiers == ["SEM-NEAR", "DC"]


def test_feature_contract_is_symbol_and_price_invariant() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "entry": [100_000.0],
            "stop": [99_000.0],
            "target": [102_000.0],
            "outcome": ["TARGET_FIRST"],
            "future_up_atr_diagnostic": [5.0],
            "control_move_atr": [1.2],
            "gross_rr": [2.0],
            "planned_target_net_r": [1.8],
            "family": ["FAILED_AUCTION_REVERSAL"],
            "entry_geometry": ["OB_FVG_OVERLAP"],
            "source_kind": ["SEMANTIC"],
            "route_kind": ["PREVIOUS_DAY_HIGH"],
            "route_scale_bucket": ["DAILY_PLUS"],
        }
    )
    features = router.causal_features(
        frame, include_common=False, include_target=True
    )
    forbidden = {
        "symbol",
        "entry",
        "stop",
        "target",
        "outcome",
        "future_up_atr_diagnostic",
    }
    assert forbidden.isdisjoint(features.columns)
    assert "gross_rr" in features
    assert "control_move_atr" in features


class _DummyBinary:
    def __init__(self, prediction: float, uncertainty: float, prior: float = 0.5):
        self.prediction = prediction
        self.uncertainty = uncertainty
        self.prior = prior
        self.ready = True

    def predict(self, features: pd.DataFrame):
        return (
            np.full(len(features), self.prediction),
            np.full(len(features), self.uncertainty),
        )


def test_common_market_only_uplift_is_removed_from_ownership() -> None:
    states = pd.DataFrame(
        {
            "period": ["fresh-2025-a"],
            "episode_id": ["episode"],
            "family": ["FAILED_AUCTION_REVERSAL"],
            "entry_geometry": ["OB_FVG_OVERLAP"],
            "source_kind": ["SEMANTIC"],
        }
    )
    models = {
        "full": _DummyBinary(0.80, 0.0),
        "local": _DummyBinary(0.55, 0.0),
        "common": _DummyBinary(0.78, 0.0),
    }
    scored = router.predict_ownership(models, states)
    assert scored.p_ownership_counterfactual.iloc[0] < 0.60
    assert scored.common_only_positive_logit_uplift.iloc[0] > 0.0


def test_target_probability_is_monotone_with_distance() -> None:
    frame = pd.DataFrame(
        {
            "period": ["fresh-a"] * 3,
            "episode_id": ["episode"] * 3,
            "gross_rr": [1.0, 2.0, 3.0],
            "target_candidate_rank": [0, 1, 2],
            "action_id": ["a", "b", "c"],
            "p_target_owned": [0.60, 0.75, 0.40],
        }
    )
    scored = router._monotone_target_probabilities(frame)
    assert np.allclose(
        scored.sort_values("gross_rr").p_target_if_filled,
        [0.60, 0.60, 0.40],
    )


def test_one_destination_is_selected_before_account_routing() -> None:
    scored = pd.DataFrame(
        {
            "period": ["fresh-a", "fresh-a"],
            "episode_id": ["episode", "episode"],
            "action_id": ["near", "far"],
            "models_ready": [True, True],
            "gross_rr": [1.4, 2.8],
            "expected_log_growth_per_hour": [0.020, 0.012],
            "expected_log_growth": [0.010, 0.018],
            "p_target_if_filled": [0.78, 0.55],
            "planned_target_net_r": [1.2, 2.5],
        }
    )
    selected = router.best_destination_per_episode(scored)
    assert len(selected) == 1
    assert selected.action_id.iloc[0] == "near"


def test_three_percent_risk_sizing_uses_stop_distance() -> None:
    sizing = router.risk_sized_quantity(
        nav=10_000.0,
        entry=100.0,
        stop=99.5,
        quantity_step=0.001,
    )
    assert abs(sizing["risk_fraction"] - 0.03) < 1e-8
    assert abs(sizing["implied_leverage"] - 6.0) < 1e-8
