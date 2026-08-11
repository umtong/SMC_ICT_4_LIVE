#!/usr/bin/env python3
"""Cross-sectional session raid with the first opposing structural objective.

The source PDFs do not instruct the trader to ignore nearer opposing structure
and always hold to the far side of an entire session range.  They repeatedly
name the *first* opposing high/low, OB, FVG, trendline or channel boundary as a
profit objective.  v13 corrects that semantic mismatch after v10 state routing:
choose the nearest already-confirmed directional-change pivot between entry and
the former far target; if it offers less than 1R, reject rather than skipping it.
"""
from __future__ import annotations

from dataclasses import replace
import os

import pandas as pd

from data import resample
from domain_v3 import Side
from market_v5 import DirectionalChangePivotDetector
import screen_v10 as _base


_ORIGINAL_ROUTE = _base.route_setups
TARGET_MINUTES = int(os.environ.get("EC_TARGET_MINUTES", "5"))
TARGET_DC_ATR = float(os.environ.get("EC_TARGET_DC_ATR", "1.0"))


def _pivots(frame: pd.DataFrame, minutes: int):
    working = frame if minutes == 5 else resample(frame, minutes)
    candles = _base.to_candles(working)
    detector = DirectionalChangePivotDetector(
        timeframe_minutes=minutes,
        atr_period=14,
        atr_multiple=TARGET_DC_ATR,
    )
    output = []
    for index, candle in enumerate(candles):
        pivot = detector.on_candle(candle, index)
        if pivot is not None:
            output.append(pivot)
    return output


def _setup_bar(frame: pd.DataFrame, observed_time_ns: int):
    observed = pd.Timestamp(int(observed_time_ns), unit="ns", tz="UTC")
    selected = frame[frame["close_time_dt"] == observed]
    return None if selected.empty else selected.iloc[-1]


def route_setups(*args, **kwargs):
    routed, diagnostics = _ORIGINAL_ROUTE(*args, **kwargs)
    five_frames = kwargs["five_frames"]
    pivots_by_symbol = {
        symbol: _pivots(frame, TARGET_MINUTES)
        for symbol, frame in five_frames.items()
    }
    output = []

    def count(key: str) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + 1

    for setup in routed:
        eligible = [
            pivot
            for pivot in pivots_by_symbol[setup.symbol]
            if pivot.observed_time_ns < setup.observed_time_ns
        ]
        if setup.side is Side.LONG:
            candidates = [
                pivot for pivot in eligible
                if pivot.side == "HIGH" and setup.entry < pivot.level <= setup.initial_target
            ]
            chosen = min(candidates, default=None, key=lambda pivot: pivot.level)
        else:
            candidates = [
                pivot for pivot in eligible
                if pivot.side == "LOW" and setup.initial_target <= pivot.level < setup.entry
            ]
            chosen = max(candidates, default=None, key=lambda pivot: pivot.level)
        if chosen is None:
            count("no_internal_structural_objective_fallback_far_boundary")
            output.append(setup)
            continue
        row = _setup_bar(five_frames[setup.symbol], setup.observed_time_ns)
        if row is None:
            count("missing_setup_bar")
            continue
        if setup.side is Side.LONG and float(row.high) >= chosen.level:
            count("first_objective_consumed_on_setup_bar")
            continue
        if setup.side is Side.SHORT and float(row.low) <= chosen.level:
            count("first_objective_consumed_on_setup_bar")
            continue
        candidate = replace(
            setup,
            family=f"{setup.family}_FIRST_DC_OBJECTIVE_{TARGET_MINUTES}M",
            causal_event_id=f"{setup.causal_event_id}:FIRST_DC:{chosen.event_time_ns}",
            initial_target=chosen.level,
            fixed_target_id=f"FIRST_DC_PIVOT:{chosen.side}:{chosen.event_time_ns}",
            context_bias=f"{setup.context_bias}|FIRST_DC_OBJECTIVE={chosen.level}",
        )
        if candidate.executable(
            candidate.initial_target,
            target_id=candidate.fixed_target_id,
            min_gross_rr=1.0,
        ) is None:
            count("first_structural_objective_rr_lt_1")
            continue
        output.append(candidate)
        count("first_structural_objective_selected")
    output.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    return output, diagnostics


_base.route_setups = route_setups


if __name__ == "__main__":
    _base.main()
