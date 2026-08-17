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
            # Old artifacts may contain tuned thresholds.  Runtime must ignore
            # them instead of recreating a 70%-target or coverage gate.
            "decision": {
                "min_probability": 0.99,
                "probability_edge": 0.40,
                "min_expected_net_r": 10.0,
            },
        }
    )


def _economics(target: float, *, fee: float = 0.0):
    return estimate_trade_economics(
        side=Side.LONG,
        entry=100.0,
        stop=99.0,
        target=target,
        tick_size=0.1,
        entry_fee_rate=fee,
        target_fee_rate=fee,
        stop_fee_rate=fee,
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
        "decision": {"kind": "target_more_likely_than_stop_and_post_cost_positive"},
    }
    model = PortableBinaryModel(document)
    assert model.probability({"x": 0.0}) == pytest.approx(0.2)
    assert model.probability({"x": 1.0}) == pytest.approx(0.8)


def test_high_rr_does_not_rescue_a_more_likely_loser() -> None:
    economics = _economics(104.0)  # 4R target
    decision = _constant_model(0.40).decide({}, economics)
    assert decision.expected_net_r > 0.0
    assert decision.target_probability < 0.50
    assert not decision.accepted
    assert decision.reason == "TARGET_NOT_MORE_LIKELY_THAN_STOP"
    assert decision.required_probability == pytest.approx(0.5)


def test_normal_rr_high_quality_plan_is_accepted_without_a_70_percent_target() -> None:
    decision = _constant_model(0.60).decide({}, _economics(101.0))  # 1R target
    assert decision.target_probability == pytest.approx(0.60)
    assert decision.expected_net_r > 0.0
    assert decision.accepted
    assert decision.reason == "TARGET_MORE_LIKELY_AND_POST_COST_POSITIVE"


def test_costs_can_invalidate_a_barely_more_likely_plan() -> None:
    decision = _constant_model(0.51).decide({}, _economics(101.0, fee=0.001))
    assert decision.target_probability > 0.50
    assert decision.expected_net_r < 0.0
    assert not decision.accepted
    assert decision.reason == "NONPOSITIVE_POST_COST_EXPECTANCY"


def test_old_probability_floors_do_not_become_runtime_targets() -> None:
    decision = _constant_model(0.60).decide({}, _economics(101.0))
    assert decision.accepted
    assert decision.required_probability == pytest.approx(0.5)


def test_shadow_model_is_not_selectable() -> None:
    model = _constant_model(0.5, status="shadow_only")
    with pytest.raises(RuntimeError):
        model.assert_selectable()
