#!/usr/bin/env python3
"""Decision-window-correct executable for candidate-1a two-sided auction V2.

The inherited data loader intentionally keeps three post-window days so positions
opened before the requested end can resolve at stop or target.  Candidate overlays had
replaced the fixed wrapper's generator and therefore also emitted *new entries* during
those three label-only days.  This wrapper restores the exact contract: entry decisions
must occur in [start, end), while their causal exits may occur after end.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import two_sided_source_auction_harvest_v2 as v2

core = v2.core


def generate_symbol(symbol, data, levels, metadata, trading_start):
    frame, counts = v2.generate_symbol(
        symbol, data, levels, metadata, trading_start
    )
    decision_end = getattr(core, "_DECISION_END_NS", None)
    if decision_end is not None and not frame.empty:
        frame = frame[
            pd.to_numeric(frame.order_time_ns, errors="coerce")
            < int(decision_end)
        ].copy()
        counts = dict(counts)
        counts["plans"] = int(len(frame))
        counts["states"] = int(frame.state_id.nunique())
        counts["decision_window_filter_restored"] = True
    return frame, counts


core.POLICY = (
    "DECISION_WINDOW_BOUNDED_SEMANTIC_SOURCE_RECLAIM_OR_ACCEPTANCE_"
    "THEN_FRESH_CONTROL_TRANSFER_FIRST_RETURN_SEQUENCE_MSS_PRICE_FLOW_"
    "RESPONSE_BRANCH_CORRECT_STOP_TO_FIRST_OPPOSING_ROUTE"
)
core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
