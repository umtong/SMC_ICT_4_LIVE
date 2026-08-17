from __future__ import annotations

from enum import Enum

import pytest

from ml1_model import (
    MODEL_SCHEMA,
    PortableBinaryModel,
    estimate_trade_economics,
)


class Side(Enum):
    LONG = 1
    SHORT = -1


def test_long_and_short_cost_geometry_is_symmetric() -> None:
    long = estimate_trade_economics(
        side=Side.LONG,
        entry=100.0,
        stop=99.0,
        target=102.0,
        tick_size=0.1,
        entry_fee_rate=0.0,
        target_fee_rate=0.0,
        stop_fee_rate=0.0,
        funding_rate=0.0,
        entry_slippage_ticks=1,
        target_slippage_ticks=1,
        stop_slippage_ticks=1,
    )
    short = estimate_trade_economics(
        side=Side.SHORT,
        entry=100.0,
        stop=101.0,
        target=98.0,
        tick_size=0.1,
        entry_fee_rate=0.0,
        target_fee_rate=0.0,
        stop_fee_rate=0.0,
        funding_rate=0.0,
        entry_slippage_ticks=1,
        target_slippage_ticks=1,
        stop_slippage_ticks=1,
    )
    assert long.gross_rr == pytest.approx(2.0)
    assert short.gross_rr == pytest.approx(2.0)
    assert long.win_net_r == pytest.approx(short.win_net_r, abs=1e-12)
    assert long.loss_net_r == pytest.approx(short.loss_net_r, abs=1e-12)
    assert 0.0 < long.break_even_probability < 1.0


def test_portable_tree_and_platt_calibration() -> None:
    document = {
        "schema": MODEL_SCHEMA,
        "model_type": "extra_trees_binary",
        "status": "trained",
        "model_id": "unit",
        "feature_names": ["x"],
        "feature_defaults": {"x": 0.0},
        "feature_clip_ranges": {"x": [-10.0, 10.0]},
        "trees": [
            {
                "nodes": [
                    {"feature": 0, "threshold": 0.5, "left": 1, "right": 2, "probability": None},
                    {"feature": -1, "threshold": 0.0, "left": -1, "right": -1, "probability": 0.2},
                    {"feature": -1, "threshold": 0.0, "left": -1, "right": -1, "probability": 0.8},
                ]
            }
        ],
        "calibration": {"kind": "platt_logit", "coefficient": 1.0, "intercept": 0.0},
        "decision": {"min_probability": 0.5, "probability_edge": 0.0, "min_expected_net_r": 0.0},
    }
    model = PortableBinaryModel(document)
    assert model.probability({"x": 0.0}) == pytest.approx(0.2)
    assert model.probability({"x": 1.0}) == pytest.approx(0.8)


def test_shadow_model_is_not_selectable_by_default() -> None:
    document = {
        "schema": MODEL_SCHEMA,
        "model_type": "constant_probability",
        "status": "shadow_only",
        "model_id": "shadow",
        "feature_names": [],
        "feature_defaults": {},
        "feature_clip_ranges": {},
        "constant_probability": 0.5,
        "calibration": {"kind": "identity"},
        "decision": {},
    }
    model = PortableBinaryModel(document)
    with pytest.raises(RuntimeError):
        model.assert_selectable()
    model.assert_selectable(allow_shadow_model=True)
