#!/usr/bin/env python3
"""Linear-time implementation of the frozen crypto HTF/LTF IFVG study.

Only implementation efficiency changes.  Scenario rules, transfer constants,
causal bar timestamps, mitigation, one-use FVG lifecycle, entry, stop, target,
cost and single-position arbitration are inherited unchanged from
``crypto_htf_ltf_ifvg_study``.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crypto_htf_ltf_ifvg_study as base


@dataclass
class Tracker:
    timeframe: str
    max_age: pd.Timedelta
    active: list[base.FVG]
    bars: list[object]

    def __init__(self, timeframe: str, max_age: pd.Timedelta):
        self.timeframe = timeframe
        self.max_age = max_age
        self.active = []
        self.bars = []

    def on_bar(self, bar) -> None:
        ts = pd.Timestamp(bar.Index)
        close = float(bar.close)
        survivors = []
        for fvg in self.active:
            if ts - fvg.formed_ts > self.max_age:
                continue
            if (fvg.side > 0 and close < fvg.midpoint) or (fvg.side < 0 and close > fvg.midpoint):
                fvg.mitigated = True
                continue
            survivors.append(fvg)
        self.active = survivors
        self.bars.append(bar)
        if len(self.bars) > 3:
            self.bars.pop(0)
        if len(self.bars) < 3:
            return
        b0, _, b2 = self.bars
        atr = float(b2.atr)
        if not math.isfinite(atr) or atr <= 0.0:
            return
        formed = ts
        bull_gap = float(b2.low) - float(b0.high)
        if bull_gap >= base.MIN_GAP_ATR * atr:
            bottom, top = float(b0.high), float(b2.low)
            self.active.append(base.FVG(
                id=f"{self.timeframe}:B:{formed.isoformat()}:{bottom:.12g}:{top:.12g}",
                side=1, bottom=bottom, top=top, midpoint=(bottom + top) / 2.0,
                formed_ts=formed, atr_at_formation=atr, timeframe=self.timeframe,
            ))
        bear_gap = float(b0.low) - float(b2.high)
        if bear_gap >= base.MIN_GAP_ATR * atr:
            bottom, top = float(b2.high), float(b0.low)
            self.active.append(base.FVG(
                id=f"{self.timeframe}:S:{formed.isoformat()}:{bottom:.12g}:{top:.12g}",
                side=-1, bottom=bottom, top=top, midpoint=(bottom + top) / 2.0,
                formed_ts=formed, atr_at_formation=atr, timeframe=self.timeframe,
            ))


def fast_detect_setups(symbol: str, panel: pd.DataFrame, year: int) -> list[base.Setup]:
    five = base.aggregate(panel, 5)
    fifteen = base.aggregate(panel, 15)
    fifteen_by_ts = {pd.Timestamp(row.Index): row for row in fifteen.itertuples()}
    t5 = Tracker("5m", base.FVG5_MAX_AGE)
    t15 = Tracker("15m", base.FVG15_MAX_AGE)
    core_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    core_end = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
    setups: list[base.Setup] = []
    rows5 = list(five.itertuples())

    for i, bar in enumerate(rows5[:-1]):
        ts = pd.Timestamp(bar.Index)
        t5.on_bar(bar)
        hbar = fifteen_by_ts.get(ts)
        if hbar is not None:
            t15.on_bar(hbar)
        if ts < core_start or ts >= core_end:
            continue
        if not t5.active or not t15.active:
            continue
        close, low, high = float(bar.close), float(bar.low), float(bar.high)
        atr5 = float(bar.atr)
        if not math.isfinite(atr5) or atr5 <= 0.0:
            continue

        chosen = None
        for f5 in t5.active:
            if f5.consumed or f5.mitigated:
                continue
            retest = (
                f5.bottom <= low <= f5.top and close > f5.top
                if f5.side > 0
                else f5.bottom <= high <= f5.top and close < f5.bottom
            )
            if not retest:
                continue
            for f15 in t15.active:
                if f15.side != f5.side or f15.mitigated:
                    continue
                buffer = base.HTF_OVERLAP_ATR * f15.atr_at_formation
                if not (f5.bottom >= f15.bottom - 1e-12 and f5.top <= f15.top + buffer + 1e-12):
                    continue
                slack = max(0.0, f15.top + buffer - f5.top) + max(0.0, f5.bottom - f15.bottom)
                if chosen is None or slack < chosen[2]:
                    chosen = (f5, f15, slack)
        if chosen is None:
            continue
        f5, f15, slack = chosen
        next_bar = rows5[i + 1]
        entry_ts = pd.Timestamp(next_bar.Index) - pd.Timedelta(minutes=4)
        entry = float(next_bar.open)
        if not math.isfinite(entry) or entry <= 0.0:
            continue
        stop_buffer = base.STOP_BUFFER_ATR * atr5
        stop = f5.bottom - stop_buffer if f5.side > 0 else f5.top + stop_buffer
        risk_abs = f5.side * (entry - stop)
        if not (math.isfinite(stop) and stop > 0.0 and risk_abs > 0.0):
            continue
        target = entry + f5.side * base.TARGET_R * risk_abs
        risk_rate = risk_abs / entry + base.COST_RATE
        reward_rate = base.TARGET_R * risk_abs / entry - base.COST_RATE
        target_net_r = reward_rate / risk_rate
        if target_net_r <= 0.0:
            continue
        f5.consumed = True
        score = (
            (f5.top - f5.bottom) / max(f5.atr_at_formation, 1e-12)
            + (f15.top - f15.bottom) / max(f15.atr_at_formation, 1e-12)
            - slack / max(f15.atr_at_formation, 1e-12)
        )
        setups.append(base.Setup(
            symbol=symbol, side=f5.side, signal_ts=ts, entry_ts=entry_ts,
            entry=entry, stop=stop, target=target, fvg5_id=f5.id, fvg15_id=f15.id,
            fvg5_gap_atr=(f5.top - f5.bottom) / f5.atr_at_formation,
            fvg15_gap_atr=(f15.top - f15.bottom) / f15.atr_at_formation,
            fvg5_age_minutes=(ts - f5.formed_ts).total_seconds() / 60.0,
            fvg15_age_minutes=(ts - f15.formed_ts).total_seconds() / 60.0,
            nesting_slack_atr=slack / f15.atr_at_formation,
            risk_rate=risk_rate, target_net_r=target_net_r, score=score,
        ))
    return setups


base.detect_setups = fast_detect_setups
base.main()
