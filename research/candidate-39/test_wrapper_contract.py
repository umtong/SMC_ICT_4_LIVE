from __future__ import annotations

import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _source(name: str) -> str:
    text = (HERE / name).read_text(encoding="utf-8")
    ast.parse(text)
    return text


def test_strategy_keeps_project_risk_and_fail_safe_contract():
    source = _source("strategy.py")
    assert "Candidate35Strategy = Candidate39Strategy" in source
    assert "ORDER_REJECTION_WHILE_POSITION_LIVE" in source
    assert "PROTECTIVE_STOP_ALREADY_CROSSED_ON_ENTRY_BAR" in source
    assert 'event_details.pop("ts_event", None)' in source
    assert "close_all_positions" in source


def test_strategy_uses_separate_confirmation_and_price_controlled_parent():
    source = _source("strategy.py")
    assert "confirmation_features_by_symbol=confirmations" in source
    assert "entry_order_type=OrderType.LIMIT" in source
    assert "entry_price=instrument.make_price(entry)" in source
    assert "BOUNDARY_NOT_RETESTED_WITHIN_ONE_AUCTION" in source


def test_strategy_requires_cost_after_target_space_before_sizing():
    source = _source("strategy.py")
    gate = source.index('"COST_SPACE_REJECTED"')
    sizing = source.index("raw_quantity = risk_budget / per_unit_loss")
    assert gate < sizing
    assert "cost_aware_net_r" in source
    assert "policy_target_r_floor" in source


def test_runner_remains_one_continuous_nautilus_harness_not_custom_simulator():
    source = _source("run.py")
    assert "candidate-35" in source
    assert "base.run(" in source
    assert "candidate-39-causal-auction-state-router-v2" in source
    assert "simulat" not in source.lower()


def test_config_has_project_risk_without_strategy_notional_or_leverage_cap():
    import json

    config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    assert config["risk_fraction"] == 0.03
    assert config["strategy"]["max_hold_minutes"] >= 30
    forbidden = {
        "max_notional",
        "leverage_cap",
        "risk_multiplier",
        "score_risk_multiplier",
    }
    assert not forbidden.intersection(config["strategy"])
