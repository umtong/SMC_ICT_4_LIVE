#!/usr/bin/env python3
"""Executable wrapper for the sequential commitment harvester."""
from __future__ import annotations

import pandas as pd

import departure_first_return_harvest_fixed as fixed
import sequential_commitment_harvest as policy

core = policy.core
_BASE_GENERATE = policy.generate_symbol


def generate_symbol(symbol, data, levels, metadata, trading_start):
    frame, counts = _BASE_GENERATE(symbol, data, levels, metadata, trading_start)
    decision_end = getattr(fixed, "_DECISION_END_NS", None)
    if decision_end is not None and not frame.empty:
        frame = frame[pd.to_numeric(frame.order_time_ns, errors="coerce") < decision_end].copy()
        counts = dict(counts)
        counts["plans"] = int(len(frame))
        counts["arm_states"] = int(frame.state_id.nunique())
    return frame, counts


core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
