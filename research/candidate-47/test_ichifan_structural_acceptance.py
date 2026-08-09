"""Dependency-free contracts for the fifteen-minute acceptance transition."""
from __future__ import annotations

import math

from ichifan_structural_acceptance_strategy import causal_signal_high_acceptance


def test_waits_before_fifteen_completed_minutes() -> None:
    for age in (0, 5, 10, 14):
        decision = causal_signal_high_acceptance(
            close=101.0,
            signal_high=100.0,
            age_minutes=age,
        )
        assert decision.state == "WAIT"
        assert decision.accepted is None


def test_requires_strict_acceptance_above_signal_high_at_fifteen_minutes() -> None:
    accepted = causal_signal_high_acceptance(
        close=100.01,
        signal_high=100.0,
        age_minutes=15,
    )
    assert accepted.state == "ACCEPTED"
    assert accepted.accepted is True

    for close in (99.99, 100.0):
        failed = causal_signal_high_acceptance(
            close=close,
            signal_high=100.0,
            age_minutes=15,
        )
        assert failed.state == "FAILED"
        assert failed.accepted is False


def test_late_first_evaluation_uses_only_current_completed_close() -> None:
    decision = causal_signal_high_acceptance(
        close=101.0,
        signal_high=100.0,
        age_minutes=18,
    )
    assert decision.state == "ACCEPTED"
    assert decision.age_minutes == 18


def test_already_evaluated_state_cannot_reclassify_position() -> None:
    decision = causal_signal_high_acceptance(
        close=90.0,
        signal_high=100.0,
        age_minutes=30,
        already_evaluated=True,
    )
    assert decision.state == "ALREADY_EVALUATED"
    assert decision.accepted is None


def test_nonfinite_nonpositive_or_negative_age_is_rejected() -> None:
    cases = [
        {"close": math.nan, "signal_high": 100.0, "age_minutes": 15},
        {"close": math.inf, "signal_high": 100.0, "age_minutes": 15},
        {"close": 0.0, "signal_high": 100.0, "age_minutes": 15},
        {"close": 100.0, "signal_high": -1.0, "age_minutes": 15},
        {"close": 100.0, "signal_high": 99.0, "age_minutes": -1},
    ]
    for case in cases:
        try:
            causal_signal_high_acceptance(**case)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid acceptance input was accepted: {case}")
