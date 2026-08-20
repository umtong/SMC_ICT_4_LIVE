#!/usr/bin/env python3
"""Executable adapter for the event-time auction policy.

The proven V5 causal loader supplies price, volume, derivative and common-market
state.  The V7 generator ignores its semantic-pool arguments and builds an independent
volatility-normalized directional-change hierarchy from the same prior-only data.
"""
from __future__ import annotations

import pandas as pd

import departure_first_return_harvest_fixed as fixed
import event_time_auction_harvest as policy

core = fixed.core


def generate_symbol(symbol, data, levels, metadata, trading_start):
    del levels, metadata
    decision_end_ns = getattr(fixed, "_DECISION_END_NS", None)
    if decision_end_ns is None:
        raise RuntimeError("decision end was not initialized")
    start = pd.Timestamp(trading_start, tz="UTC") if pd.Timestamp(trading_start).tzinfo is None else pd.Timestamp(trading_start).tz_convert("UTC")
    end = pd.Timestamp(decision_end_ns, unit="ns", tz="UTC")
    frame = policy.generate_symbol(
        symbol,
        data,
        start.date().isoformat(),
        end.date().isoformat(),
        core.CONTRACTS[symbol].tick_size,
    )
    counts = {
        "plans": int(len(frame)),
        "states": int(frame.state_id.nunique()) if not frame.empty else 0,
        "episodes": int(frame.episode_id.nunique()) if not frame.empty else 0,
    }
    return frame, counts


core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
