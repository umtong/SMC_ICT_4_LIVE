"""Dependency-free contracts for the fifteen-minute positive response."""
from __future__ import annotations

import math

from ichifan_structural_response_strategy import causal_positive_response


def test_waits_before_fifteen_completed_minutes() -> None:
    for age in (0, 5, 10, 14):
        decision = causal_positive_response(
            close=101.0,
            entry=100.0,
            age_minutes=age,
        )
        assert decision.state == "WAIT"
        assert decision.positive is None


def test_requires_strictly_positive_response_at_fifteen_minutes() -> None:
    positive = causal_positive_response(
        close=100.01,
        entry=100.0,
        age_minutes=15,
    )
    assert positive.state == "POSITIVE"
    assert positive.positive is True

    for close in (99.99, 100.0):
        failed = causal_positive_response(
            close=close,
            entry=100.0,
            age_minutes=15,
        )
        assert failed.state == "FAILED"
        assert failed.positive is False


def test_late_first_evaluation_uses_only_current_completed_close() -> None:
    decision = causal_positive_response(
        close=101.0,
        entry=100.0,
        age_minutes=18,
    )
    assert decision.state == "POSITIVE"
    assert decision.age_minutes == 18


def test_already_evaluated_position_cannot_be_reclassified() -> None:
    decision = causal_positive_response(
        close=90.0,
        entry=100.0,
        age_minutes=60,
        already_evaluated=True,
    )
    assert decision.state == "ALREADY_EVALUATED"
    assert decision.positive is None


def test_invalid_values_or_negative_age_are_rejected() -> None:
    cases = [
        {"close": math.nan, "entry": 100.0, "age_minutes": 15},
        {"close": math.inf, "entry": 100.0, "age_minutes": 15},
        {"close": 0.0, "entry": 100.0, "age_minutes": 15},
        {"close": 100.0, "entry": -1.0, "age_minutes": 15},
        {"close": 100.0, "entry": 99.0, "age_minutes": -1},
    ]
    for case in cases:
        try:
            causal_positive_response(**case)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid response input was accepted: {case}")
