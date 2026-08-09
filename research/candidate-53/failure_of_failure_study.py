#!/usr/bin/env python3
"""Failure-of-failure continuation study derived from Candidate 53 sweep losses.

The corrected sweep/reclaim reversal family fails because many high-volume,
aggressor-sponsored breaches briefly reclaim the old range and then resume in
the original breach direction.  Rather than threshold-tune the losing reversal,
this study changes the causal hypothesis:

external-level sweep with flow in breach direction -> reclaim and a strictly
later rejection attempt -> the rejection itself fails -> a later one-minute bar
closes through the original sweep extreme with aggressor flow again aligned to
the original breach -> enter continuation.  Invalidation is the old range
boundary; a second close back through that boundary means the re-break did not
establish a new auction. Target is solved for +2R after the same 21 bp cost.

The sweep/reclaim/rejection detector is reused unchanged except for correcting
v1's known flow-polarity implementation bug. Outcome paths are descriptive only;
NautilusTrader remains mandatory for promoted execution/accounting.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import sweep_reclaim_study as base

COST_RATE = base.COST_RATE
TARGET_NET_R = 2.0
REBREAK_SEARCH_MINUTES = 15
MAX_HOLD_MINUTES = 180


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    source_variant: str
    side: int
    sweep_ts: pd.Timestamp
    rejection_confirm_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    entry: float
    stop: float
    target: float
    old_boundary: float
    sweep_extreme: float
    rebreak_flow: float
    sweep_breach_atr: float
    sweep_volume_ratio: float
    planned_loss_rate: float
    target_distance_rate: float
    score: float


@dataclass(frozen=True, slots=True)
class Scored:
    candidate: Candidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float


def _corrected_source_candidate(symbol: str, panel: pd.DataFrame, i: int, range_minutes: int, reversal_side: int):
    flow_col = panel.columns.get_loc("flow")
    actual = float(panel.iat[i, flow_col])
    panel.iat[i, flow_col] = -actual
    try:
        candidate = base._candidate_for_event(symbol, panel, i, range_minutes, reversal_side)
    finally:
        panel.iat[i, flow_col] = actual
    return candidate


def source_events(symbol: str, panel: pd.DataFrame) -> list[base.Candidate]:
    result = []
    seen = set()
    for i in range(base.MIN_HISTORY + 5, len(panel) - base.CONFIRM_BARS - 1):
        row = panel.iloc[i]
        for minutes in (60, 240):
            ph, pl = float(row[f"prior_high_{minutes}"]), float(row[f"prior_low_{minutes}"])
            high, low = float(row["perp_high"]), float(row["perp_low"])
            directions = []
            if math.isfinite(ph) and high > ph: directions.append(-1)  # later reversal short, breach long
            if math.isfinite(pl) and low < pl: directions.append(1)   # later reversal long, breach short
            for reversal_side in directions:
                c = _corrected_source_candidate(symbol, panel, i, minutes, reversal_side)
                if c is None: continue
                key = (c.symbol, c.sweep_ts, reversal_side)
                # Same causal sweep may qualify against both 60m and 240m. Keep
                # the longer external reference when duplicated.
                old = seen
                if key in old: continue
                seen.add(key); result.append(c)
    return result


def _geometry(side: int, entry: float, stop: float):
    raw_risk = side * (entry - stop) / entry
    if not math.isfinite(raw_risk) or raw_risk <= 0.0: return None
    planned = raw_risk + COST_RATE
    distance = TARGET_NET_R * planned + COST_RATE
    target = entry * (1.0 + side * distance)
    if target <= 0.0 or not math.isfinite(target): return None
    return target, planned, distance


def build(symbol: str, panel: pd.DataFrame) -> list[Candidate]:
    result = []
    for event in source_events(symbol, panel):
        side = -event.side  # original breach direction
        start_i = int(panel.index.searchsorted(event.entry_ts, side="right"))
        end_i = min(start_i + REBREAK_SEARCH_MINUTES, len(panel))
        for i in range(start_i, end_i):
            row = panel.iloc[i]
            close, open_, flow = float(row["perp_close"]), float(row["perp_open"]), float(row["flow"])
            sweep_extreme = float(event.stop)
            if side > 0:
                rebreak = close > sweep_extreme and close > open_ and flow > 0.0
                old_boundary = float(event.prior_high)
            else:
                rebreak = close < sweep_extreme and close < open_ and flow < 0.0
                old_boundary = float(event.prior_low)
            if not rebreak: continue
            geometry = _geometry(side, close, old_boundary)
            if geometry is None: break
            target, planned, distance = geometry
            score = float(event.sweep_breach_atr) * float(event.sweep_volume_ratio_to_threshold) * (1.0 + abs(flow))
            result.append(Candidate(
                symbol=symbol, source_variant=event.variant, side=side,
                sweep_ts=event.sweep_ts, rejection_confirm_ts=event.entry_ts,
                entry_ts=pd.Timestamp(row["minute"]), entry=close, stop=old_boundary,
                target=target, old_boundary=old_boundary, sweep_extreme=sweep_extreme,
                rebreak_flow=flow, sweep_breach_atr=float(event.sweep_breach_atr),
                sweep_volume_ratio=float(event.sweep_volume_ratio_to_threshold),
                planned_loss_rate=planned, target_distance_rate=distance, score=score,
            ))
            break
    return result


def arbitrate(candidates: Iterable[Candidate]) -> list[Candidate]:
    ordered=sorted(candidates,key=lambda c:(c.entry_ts,-c.score,c.symbol))
    out=[]; bucket=[]; anchor=None
    for c in ordered:
        if anchor is None or c.entry_ts-anchor<=pd.Timedelta(minutes=3):
            if anchor is None: anchor=c.entry_ts
            bucket.append(c); continue
        out.append(max(bucket,key=lambda x:x.score)); bucket=[c]; anchor=c.entry_ts
    if bucket: out.append(max(bucket,key=lambda x:x.score))
    return out


def score(c: Candidate, panel: pd.DataFrame) -> Scored:
    start=int(panel.index.searchsorted(c.entry_ts,side="right")); end=min(start+MAX_HOLD_MINUTES,len(panel))
    exit_ts,exit_price,reason=c.entry_ts,c.entry,"TIME"
    for i in range(start,end):
        row=panel.iloc[i]; high=float(row["perp_high"]); low=float(row["perp_low"]); close=float(row["perp_close"])
        if c.side>0:
            if low<=c.stop: exit_ts,exit_price,reason=pd.Timestamp(row["minute"]),c.stop,"STOP"; break
            if high>=c.target: exit_ts,exit_price,reason=pd.Timestamp(row["minute"]),c.target,"TARGET"; break
        else:
            if high>=c.stop: exit_ts,exit_price,reason=pd.Timestamp(row["minute"]),c.stop,"STOP"; break
            if low<=c.target: exit_ts,exit_price,reason=pd.Timestamp(row["minute"]),c.target,"TARGET"; break
        exit_ts,exit_price=pd.Timestamp(row["minute"]),close
    net=c.side*(exit_price/c.entry-1.0)-COST_RATE
    return Scored(c,exit_ts,reason,exit_price,net,net/c.planned_loss_rate)


def summarize(rows: list[Scored]):
    if not rows: return {"trades":0,"wins":0,"win_rate":0.0,"mean_net_r":0.0,"profit_factor_r":0.0,"targets":0,"stops":0}
    r=np.array([x.net_r for x in rows]); gains=r[r>0].sum(); losses=-r[r<0].sum()
    return {"trades":len(rows),"wins":int((r>0).sum()),"win_rate":float((r>0).mean()),"mean_net_r":float(r.mean()),
            "median_net_r":float(np.median(r)),"profit_factor_r":float(gains/losses) if losses>0 else 999.0,
            "targets":sum(x.exit_reason=="TARGET" for x in rows),"stops":sum(x.exit_reason=="STOP" for x in rows)}


def main():
    p=argparse.ArgumentParser(); p.add_argument("--start",required=True); p.add_argument("--end",required=True); p.add_argument("--cache",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    start=date.fromisoformat(a.start); end=date.fromisoformat(a.end); warm=start-timedelta(days=2)
    panels={s:base.load_symbol(s,warm,end,a.cache) for s in base.SYMBOLS}
    candidates=[]
    for s,panel in panels.items(): candidates.extend(c for c in build(s,panel) if c.entry_ts.date()>=start)
    selected=arbitrate(candidates); accepted=[]; occupied=None
    for c in selected:
        if occupied is not None and c.entry_ts<=occupied: continue
        x=score(c,panels[c.symbol]); accepted.append(x); occupied=x.exit_ts
    a.output.mkdir(parents=True,exist_ok=True)
    result={"study":"candidate-53-failure-of-failure-continuation","start":a.start,"end":a.end,"cost_rate":COST_RATE,
            "detected":len(candidates),"arbitrated":len(selected),"single_slot":len(accepted),"summary":summarize(accepted)}
    def conv(v):
        if isinstance(v,pd.Timestamp): return v.isoformat()
        if isinstance(v,np.integer): return int(v)
        if isinstance(v,np.floating): return float(v)
        raise TypeError(type(v).__name__)
    payload=[{**asdict(x.candidate),**{k:v for k,v in asdict(x).items() if k!="candidate"}} for x in accepted]
    (a.output/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n")
    (a.output/"trades.json").write_text(json.dumps(payload,indent=2,sort_keys=True,default=conv,allow_nan=False)+"\n")
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))

if __name__=="__main__": main()
