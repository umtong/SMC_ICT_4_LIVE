#!/usr/bin/env python3
"""Causal 3% sizing from conditional adverse entry-delay expectation.

The V34 arithmetic estimator averaged favorable and unchanged one-bar entry
delays as zero. That estimates unconditional average slippage but understates
loss when the realized delayed fill is adverse. The project loss budget must use
the expected fill conditional on paying adverse execution cost.

This adapter changes only that expectation. It uses the arithmetic mean of
strictly positive, direction-signed close-to-next-close transitions from the
same completed past window. Target, stop, fees, current NAV, 3% budget, quantity
precision and every NautilusTrader order/account mechanism remain unchanged.
"""
from __future__ import annotations

import math

import nt_expected_fill_only_risk_sizing as base
from nt_expected_fill_risk_sizing import EXPECTED_GAP_MIN_OBSERVATIONS
from nt_expected_fill_risk_sizing import directional_entry_excursions


FILL_EXPECTATION_CONTRACT = (
    "conditional_mean_positive_completed_directional_close_transition_plus_one_tick"
)


def causal_conditional_adverse_entry_deterioration(
    rows: list[dict[str, float | int]],
    side: int,
) -> tuple[float, int]:
    """Mean adverse transition conditional on an adverse transition occurring."""

    values = directional_entry_excursions(rows, side)
    if len(values) < EXPECTED_GAP_MIN_OBSERVATIONS:
        return float("nan"), len(values)
    adverse = [value for value in values if math.isfinite(value) and value > 0.0]
    if not adverse:
        return float("nan"), len(values)
    return sum(adverse) / len(adverse), len(values)


base.causal_expected_entry_deterioration = (
    causal_conditional_adverse_entry_deterioration
)
base.FILL_EXPECTATION_CONTRACT = FILL_EXPECTATION_CONTRACT
conditional_adverse_fill_submit_bracket = base.expected_fill_only_submit_bracket


__all__ = [
    "FILL_EXPECTATION_CONTRACT",
    "causal_conditional_adverse_entry_deterioration",
    "conditional_adverse_fill_submit_bracket",
]
