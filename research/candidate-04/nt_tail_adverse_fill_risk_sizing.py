#!/usr/bin/env python3
"""Causal tail-adverse delayed-fill sizing for the 3% NAV loss contract.

The prior conditional-mean estimator remained unbiased for average adverse
fills but understated the V55 loss budget when the delayed market fill landed
in the adverse tail. This module changes no signal, direction, stop, target,
fee, slippage, venue or portfolio rule. It replaces only the expected adverse
entry deterioration used in quantity sizing with the past-only 95th percentile
of strictly adverse completed close-to-next-close transitions from the same
720-bar window. NautilusTrader still owns all execution and PnL.
"""
from __future__ import annotations

import math

import numpy as np

import nt_expected_fill_only_risk_sizing as base
from nt_expected_fill_risk_sizing import EXPECTED_GAP_MIN_OBSERVATIONS
from nt_expected_fill_risk_sizing import directional_entry_excursions

ADVERSE_ENTRY_QUANTILE = 0.95
FILL_EXPECTATION_CONTRACT = (
    "past_only_q95_positive_completed_directional_close_transition_plus_one_tick"
)


def causal_tail_adverse_entry_deterioration(
    rows: list[dict[str, float | int]],
    side: int,
) -> tuple[float, int]:
    """Past-only q95 of adverse delayed-fill transitions."""

    values = directional_entry_excursions(rows, side)
    if len(values) < EXPECTED_GAP_MIN_OBSERVATIONS:
        return float("nan"), len(values)
    adverse = np.asarray(
        [value for value in values if math.isfinite(value) and value > 0.0],
        dtype=float,
    )
    if adverse.size < max(30, EXPECTED_GAP_MIN_OBSERVATIONS // 4):
        return float("nan"), len(values)
    estimate = float(np.quantile(adverse, ADVERSE_ENTRY_QUANTILE))
    return estimate, len(values)


base.causal_expected_entry_deterioration = causal_tail_adverse_entry_deterioration
base.FILL_EXPECTATION_CONTRACT = FILL_EXPECTATION_CONTRACT
q95_adverse_fill_submit_bracket = base.expected_fill_only_submit_bracket


__all__ = [
    "ADVERSE_ENTRY_QUANTILE",
    "FILL_EXPECTATION_CONTRACT",
    "causal_tail_adverse_entry_deterioration",
    "q95_adverse_fill_submit_bracket",
]
