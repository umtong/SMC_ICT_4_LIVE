#!/usr/bin/env python3
"""Run the quality control-transfer policy with TP and SL as the only exits.

The existing control-transfer detector is reused because it encodes the observed
sequence: semantic liquidity raid, reclaim, inward initiative, real pullback and
reacceleration.  Only its vertical time barrier is replaced.  A position whose target
and stop are both untouched at the end of available label data remains CENSORED_OPEN;
it is not liquidated and contributes no realized R.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import control_transfer_research as core


def _barrier_only_label(
    frame: pd.DataFrame,
    entry_index: int,
    side: str,
    entry: float,
    stop: float,
    target: float,
    tick: float,
) -> core.Label:
    sign = 1.0 if side == "LONG" else -1.0
    actual_entry = float(frame.open.iloc[entry_index]) + sign * core.ENTRY_SLIPPAGE_TICKS * tick
    stop_fill = float(stop) - sign * core.STOP_SLIPPAGE_TICKS * tick
    risk_price = abs(actual_entry - stop_fill)
    if not math.isfinite(risk_price) or risk_price <= 0.0:
        raise RuntimeError("invalid barrier-only risk geometry")
    raw_stop = sign * (stop_fill - actual_entry) / risk_price - (
        core.ENTRY_FEE * abs(actual_entry) + core.STOP_FEE * abs(stop_fill)
    ) / risk_price
    normalization = max(abs(raw_stop), 1e-12)
    raw_target = sign * (float(target) - actual_entry) / risk_price - (
        core.ENTRY_FEE * abs(actual_entry) + core.TARGET_FEE * abs(target)
    ) / risk_price
    target_r = raw_target / normalization

    high = frame.high.to_numpy(dtype=float, copy=False)
    low = frame.low.to_numpy(dtype=float, copy=False)
    if side == "LONG":
        stop_hits = low[entry_index:] <= float(stop)
        target_hits = high[entry_index:] >= float(target)
    else:
        stop_hits = high[entry_index:] >= float(stop)
        target_hits = low[entry_index:] <= float(target)
    first_stop = int(np.argmax(stop_hits)) if bool(stop_hits.any()) else None
    first_target = int(np.argmax(target_hits)) if bool(target_hits.any()) else None

    if first_stop is None and first_target is None:
        end = len(frame) - 1
        return core.Label(
            "CENSORED_OPEN",
            float("nan"),
            frame.index[end] + pd.Timedelta(minutes=1),
            end - entry_index + 1,
            target_r,
        )
    # One-minute OHLC cannot order both prints inside the same bar. Do not credit a
    # target when the stop may have printed first.
    if first_stop is not None and (first_target is None or first_stop <= first_target):
        relative = first_stop
        outcome = "STOP_FIRST"
        result = -1.0
    else:
        relative = int(first_target)
        outcome = "TARGET_FIRST"
        result = target_r
    position = entry_index + int(relative)
    return core.Label(
        outcome,
        result,
        frame.index[position] + pd.Timedelta(minutes=1),
        int(relative) + 1,
        target_r,
    )


core._label = _barrier_only_label

# This import patches the event detector with the durable-response and non-opposing
# multiscale-structure semantics found in the trade-by-trade chart clinic.
import quality_control_transfer  # noqa: E402,F401

if __name__ == "__main__":
    core.main()
