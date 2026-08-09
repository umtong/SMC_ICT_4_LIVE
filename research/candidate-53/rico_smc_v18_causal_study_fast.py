#!/usr/bin/env python3
"""Performance-only wrapper for the frozen SMC V18.2 replication.

Economic rules and source-artifact separation are unchanged.  The original
study walked 1m bars in Python for every candidate trade; this wrapper replaces
only that loop with NumPy first-touch lookup so the exact same stop-first path
can finish quickly.  It is an implementation-efficiency change, not an alpha
change.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

import rico_smc_v18_causal_study as base

_CACHE: dict[int, tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]] = {}


def _arrays(panel: pd.DataFrame):
    key=id(panel)
    cached=_CACHE.get(key)
    if cached is None:
        cached=(
            panel.index,
            panel["perp_high"].to_numpy(dtype=float),
            panel["perp_low"].to_numpy(dtype=float),
        )
        _CACHE[key]=cached
    return cached


def score_trade(variant,symbol,side,signal_ts,entry_ts,entry,stop,target,ob_high,ob_low,panel):
    index, highs, lows=_arrays(panel)
    pos=int(index.searchsorted(entry_ts,side="left"))
    if pos>=len(index) or index[pos] != entry_ts:
        return None
    if side>0:
        stop_hits=np.flatnonzero(lows[pos:] <= stop)
        target_hits=np.flatnonzero(highs[pos:] >= target)
    else:
        stop_hits=np.flatnonzero(highs[pos:] >= stop)
        target_hits=np.flatnonzero(lows[pos:] <= target)
    s=int(stop_hits[0]) if stop_hits.size else 10**18
    t=int(target_hits[0]) if target_hits.size else 10**18
    if s==10**18 and t==10**18:
        return None
    if s <= t:
        rel=s; px=float(stop); reason="stop"
    else:
        rel=t; px=float(target); reason="target"
    exit_pos=pos+rel; exit_ts=pd.Timestamp(index[exit_pos])
    gross=side*(px/entry-1.0); net=gross-base.RT_COST
    planned=abs(entry-stop)/entry+base.RT_COST
    return base.Trade(
        variant,symbol,pd.Timestamp(signal_ts),pd.Timestamp(entry_ts),exit_ts,side,
        float(ob_high),float(ob_low),float(entry),float(stop),float(target),px,reason,
        float(gross),float(net),float(net/planned) if planned>0 else math.nan,
        int((exit_ts-pd.Timestamp(entry_ts)).total_seconds()//60),
    )


if __name__ == "__main__":
    base.score_trade=score_trade
    base.main()
