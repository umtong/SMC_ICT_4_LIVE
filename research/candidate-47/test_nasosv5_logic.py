"""Dependency-free exact-default contracts for public NASOSv5."""
from __future__ import annotations

import math

from nasosv5_logic import NasosSnapshot, exact_nasosv5_decision


def _snapshot(**overrides):
    values = {
        "close": 90.0,
        "low": 90.0,
        "volume": 10.0,
        "ema_buy": 100.0,
        "ema_sell": 100.0,
        "ema_fast_ewo": 104.0,
        "ema_slow_ewo": 100.0,
        "rsi_fast": 20.0,
        "rsi": 50.0,
        "objective_15m_high": 100.0,
    }
    values.update(overrides)
    return NasosSnapshot(**values)


def test_exact_ewo1_branch_is_reachable() -> None:
    decision = exact_nasosv5_decision(_snapshot())
    assert decision.ewo1 is True
    assert decision.ewo2 is False
    assert decision.ewolow is False
    assert decision.actionable is True
    assert decision.tag == "ewo1"


def test_exact_ewo2_branch_is_reachable() -> None:
    decision = exact_nasosv5_decision(
        _snapshot(
            ema_fast_ewo=96.0,
            ema_slow_ewo=100.0,
            rsi=20.0,
        )
    )
    assert decision.ewo1 is False
    assert decision.ewo2 is True
    assert decision.ewolow is False
    assert decision.actionable is True
    assert decision.tag == "ewo2"


def test_exact_ewolow_branch_is_reachable() -> None:
    decision = exact_nasosv5_decision(
        _snapshot(
            ema_fast_ewo=85.0,
            ema_slow_ewo=100.0,
            rsi=50.0,
        )
    )
    assert decision.ewo1 is False
    assert decision.ewo2 is False
    assert decision.ewolow is True
    assert decision.actionable is True
    assert decision.tag == "ewolow"


def test_recent_15m_objective_space_suppresses_an_otherwise_valid_entry() -> None:
    decision = exact_nasosv5_decision(
        _snapshot(objective_15m_high=93.0)
    )
    assert decision.raw_entry is True
    assert decision.suppressed_no_profit_space is True
    assert decision.actionable is False
    assert decision.profit_space < 0.037


def test_boundary_is_strictly_less_than_source_profit_threshold() -> None:
    objective = 90.0 * 1.037
    decision = exact_nasosv5_decision(
        _snapshot(objective_15m_high=objective)
    )
    assert decision.raw_entry is True
    assert decision.suppressed_no_profit_space is False
    assert decision.actionable is True


def test_nonfinite_or_nonpositive_input_is_rejected() -> None:
    cases = [
        {"close": math.nan},
        {"low": 0.0},
        {"volume": -1.0},
        {"ema_buy": math.inf},
        {"objective_15m_high": -1.0},
    ]
    for overrides in cases:
        try:
            exact_nasosv5_decision(_snapshot(**overrides))
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid NASOSv5 input accepted: {overrides}")
