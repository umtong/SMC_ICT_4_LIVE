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


def _constant_model(probability: float, *, status: str = "trained") -> PortableBinaryModel:
    return PortableBinaryModel(
        {
            "schema": MODEL_SCHEMA,
            "model_type": "constant_probability",
            "status": status,
            "model_id": f"p-{probability}",
            "feature_names": [],
            "feature_defaults": {},
            "feature_clip_ranges": {},
            "constant_probability": probability,
            "calibration": {"kind": "identity"},
            # Old artifacts may still contain these fields. Runtime must not
            # turn them into an extra conservative gate.
            "decision": {
                "min_probability": 0.99,
                "probability_edge": 0.40,
                "min_expected_net_r": 10.0,
            },
        }
    )


def _two_r_economics():
    return estimate_trade_economics(
        side=Side.LONG,
        entry=100.0,
        stop=99.0,
        target=102.0,
        tick_size=0.1,
        entry_fee_rate=0.0,
        target_fee_rate=0.0,
        stop_fee_rate=0.0,
        funding_rate=0.0,
    )


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
        "decision": {"kind": "positive_post_cost_expectancy"},
    }
    model = PortableBinaryModel(document)
    assert model.probability({"x": 0.0}) == pytest.approx(0.2)
    assert model.probability({"x": 1.0}) == pytest.approx(0.8)


def test_old_probability_floors_do_not_create_an_extra_runtime_gate() -> None:
    economics = _two_r_economics()
    decision = _constant_model(0.40).decide({}, economics)
    assert economics.break_even_probability == pytest.approx(1.0 / 3.0)
    assert decision.target_probability < 0.50
    assert decision.expected_net_r > 0.0
    assert decision.accepted
    assert decision.required_probability == pytest.approx(economics.break_even_probability)


def test_nonpositive_expectancy_is_not_misrepresented_as_alpha() -> None:
    decision = _constant_model(0.30).decide({}, _two_r_economics())
    assert decision.expected_net_r < 0.0
    assert not decision.accepted
    assert decision.reason == "NONPOSITIVE_MODEL_EXPECTANCY"


def test_shadow_model_is_not_selectable() -> None:
    model = _constant_model(0.5, status="shadow_only")
    with pytest.raises(RuntimeError):
        model.assert_selectable()
