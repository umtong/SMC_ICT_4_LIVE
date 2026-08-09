"""Dependency-free contracts for Candidate 47 structural ichiFan risk."""
from __future__ import annotations

import math

from ichifan_structural_strategy import causal_structural_stop


def test_stop_is_below_every_causal_support_and_entry() -> None:
    stop, geometry = causal_structural_stop(
        entry=100.0,
        signal_bar_low=98.5,
        trend_close_90m=97.0,
        cloud_a=96.5,
        cloud_b=97.5,
    )
    assert stop == 97.0
    assert stop < 100.0
    assert stop <= 98.5
    assert stop <= 97.0
    assert stop <= max(96.5, 97.5)
    assert math.isclose(geometry["structural_stop_fraction"], 0.03)


def test_source_emergency_stop_is_maximum_distance() -> None:
    stop, geometry = causal_structural_stop(
        entry=100.0,
        signal_bar_low=85.0,
        trend_close_90m=84.0,
        cloud_a=82.0,
        cloud_b=83.0,
    )
    assert stop == 90.0
    assert geometry["causal_floor"] == 83.0
    assert geometry["emergency_stop"] == 90.0
    assert math.isclose(geometry["structural_stop_fraction"], 0.10)


def test_future_or_nonfinite_input_is_rejected() -> None:
    for value in (math.nan, math.inf, -1.0, 0.0):
        try:
            causal_structural_stop(
                entry=100.0,
                signal_bar_low=value,
                trend_close_90m=98.0,
                cloud_a=97.0,
                cloud_b=96.0,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid value {value} was accepted")


def test_stop_cannot_be_above_entry() -> None:
    try:
        causal_structural_stop(
            entry=100.0,
            signal_bar_low=101.0,
            trend_close_90m=102.0,
            cloud_a=103.0,
            cloud_b=104.0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("above-entry long stop was accepted")
