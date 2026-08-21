#!/usr/bin/env python3
"""Decision-window-correct executable for candidate-1a two-sided auction V2.

The inherited loader deliberately keeps three post-window days so entries opened before
``end`` can resolve at stop or target.  Candidate overlays replaced the fixed wrapper's
generator and accidentally emitted new entries during those label-only days.  This
wrapper restores [start, end) decisions while retaining post-end exits.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

import two_sided_source_auction_harvest_v2 as v2

core = v2.core


def _decision_end_ns() -> int | None:
    # Workflows expose END.  Direct CLI use is also supported so the contract does not
    # depend on a hidden module-global in one layer of the inherited wrapper stack.
    text = os.environ.get("DECISION_END") or os.environ.get("END")
    if not text:
        for index, token in enumerate(sys.argv[:-1]):
            if token == "--end":
                text = sys.argv[index + 1]
                break
    if not text:
        return None
    return int(pd.Timestamp(text, tz="UTC").value)


def generate_symbol(symbol, data, levels, metadata, trading_start):
    frame, counts = v2.generate_symbol(
        symbol, data, levels, metadata, trading_start
    )
    decision_end = _decision_end_ns()
    if decision_end is not None and not frame.empty:
        frame = frame[
            pd.to_numeric(frame.order_time_ns, errors="coerce") < decision_end
        ].copy()
        counts = dict(counts)
        counts["plans"] = int(len(frame))
        counts["states"] = int(frame.state_id.nunique())
        counts["decision_window_filter_restored"] = True
        counts["decision_end_ns"] = int(decision_end)
    return frame, counts


core.POLICY = (
    "DECISION_WINDOW_BOUNDED_SEMANTIC_SOURCE_RECLAIM_OR_ACCEPTANCE_"
    "THEN_FRESH_CONTROL_TRANSFER_FIRST_RETURN_SEQUENCE_MSS_PRICE_FLOW_"
    "RESPONSE_BRANCH_CORRECT_STOP_TO_FIRST_OPPOSING_ROUTE"
)
core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
