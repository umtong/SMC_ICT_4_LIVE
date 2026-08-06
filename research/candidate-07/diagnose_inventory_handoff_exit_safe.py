#!/usr/bin/env python3
"""Exit-safe wrapper for the inventory-handoff diagnostic.

This changes no signal, route, stop, target, state transition or data input.
It fixes one diagnostic implementation error: favorable/adverse excursion must
be measured only through the first target/stop event, not after the hypothetical
position has already terminated.  Full-horizon excursions are retained under
explicitly separate diagnostic fields to identify direction-right/timing-wrong
paths without contaminating the route gate.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import diagnose_inventory_handoff as base


def _exit_safe_path_result(
    bars: pd.DataFrame,
    *,
    start_index: int,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    max_hold_bars: int,
) -> tuple[dict[str, Any], int]:
    risk = entry - stop if direction == "LONG" else stop - entry
    future = bars.iloc[start_index + 1 : start_index + 1 + max_hold_bars]
    if future.empty:
        return {
            "outcome": "TIMEOUT",
            "timestamp_ns": None,
            "mfe_r": None,
            "mae_r": None,
            "terminal_close_r": None,
            "full_horizon_mfe_r": None,
            "full_horizon_mae_r": None,
            "target_reached_after_stop": False,
        }, start_index

    if direction == "LONG":
        favorable_all = (future["high"] - entry) / risk
        adverse_all = (entry - future["low"]) / risk
        close_all = (future["close"] - entry) / risk
    else:
        favorable_all = (entry - future["low"]) / risk
        adverse_all = (future["high"] - entry) / risk
        close_all = (entry - future["close"]) / risk

    outcome = "TIMEOUT"
    event_ns: int | None = None
    event_position = len(future.index) - 1
    block_until = int(future.index[-1])
    for position, (index, row) in enumerate(future.iterrows()):
        if direction == "LONG":
            stop_hit = float(row["low"]) <= stop
            target_hit = float(row["high"]) >= target
        else:
            stop_hit = float(row["high"]) >= stop
            target_hit = float(row["low"]) <= target
        if stop_hit and target_hit:
            outcome = "AMBIGUOUS_SAME_BAR"
        elif stop_hit:
            outcome = "STOP"
        elif target_hit:
            outcome = "TARGET"
        else:
            continue
        event_ns = int(row["timestamp_ns"])
        event_position = position
        block_until = int(index)
        break

    observed = future.iloc[: event_position + 1]
    if direction == "LONG":
        favorable_observed = (observed["high"] - entry) / risk
        adverse_observed = (entry - observed["low"]) / risk
        close_observed = (observed["close"] - entry) / risk
    else:
        favorable_observed = (entry - observed["low"]) / risk
        adverse_observed = (observed["high"] - entry) / risk
        close_observed = (entry - observed["close"]) / risk

    target_after_stop = False
    if outcome == "STOP" and event_position + 1 < len(future.index):
        after = future.iloc[event_position + 1 :]
        target_after_stop = bool(
            (after["high"] >= target).any()
            if direction == "LONG"
            else (after["low"] <= target).any()
        )

    return {
        "outcome": outcome,
        "timestamp_ns": event_ns,
        "mfe_r": float(favorable_observed.max()),
        "mae_r": float(adverse_observed.max()),
        "terminal_close_r": float(close_observed.iloc[-1]),
        "full_horizon_mfe_r": float(favorable_all.max()),
        "full_horizon_mae_r": float(adverse_all.max()),
        "target_reached_after_stop": target_after_stop,
    }, block_until


base._path_result = _exit_safe_path_result


if __name__ == "__main__":
    raise SystemExit(base.main())
