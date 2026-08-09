#!/usr/bin/env python3
"""Correct Candidate 53 sweep study's aggressor-flow polarity.

The reversal side is opposite the sweep direction. The v1 screen mistakenly
required aggressor flow aligned with the later reversal side. This adapter
changes only that implementation bug: a high sweep that later rejects short
must have positive/buy aggressor flow during the sweep; a low sweep that later
rejects long must have negative/sell aggressor flow.
"""
from __future__ import annotations

from dataclasses import replace

import sweep_reclaim_study as base

_original = base._candidate_for_event
_flow_column = None


def _corrected(symbol, panel, i, range_minutes, side):
    global _flow_column
    if _flow_column is None:
        _flow_column = panel.columns.get_loc("flow")
    original_flow = float(panel.iat[i, _flow_column])
    # v1 required side*flow>0; negate only for the predicate so that this is
    # equivalent to side*actual_flow<0, then restore both data and diagnostics.
    panel.iat[i, _flow_column] = -original_flow
    try:
        candidate = _original(symbol, panel, i, range_minutes, side)
    finally:
        panel.iat[i, _flow_column] = original_flow
    if candidate is None:
        return None
    return replace(candidate, sweep_flow=original_flow)


base._candidate_for_event = _corrected
base.main()
