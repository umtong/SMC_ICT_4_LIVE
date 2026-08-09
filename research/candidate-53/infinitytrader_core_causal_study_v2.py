#!/usr/bin/env python3
"""Implementation-corrected detector for the frozen INFINITYTRADER core study.

No economic rule changes versus ``infinitytrader_core_causal_study.py``.
This wrapper only fixes the special-long prior-close test and makes the final
15m child lookup O(1). It then runs the original frozen scoring/reporting path.
"""
from __future__ import annotations

import math
import pandas as pd

import infinitytrader_core_causal_study as base


def detect(symbol: str, panel: pd.DataFrame, year: int):
    h4, h1, m15 = base.indicators(panel)
    start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    work = h4.copy()
    work["prior_close"] = work["close"].shift(1)
    left = work.reset_index().rename(columns={work.index.name or "index": "ts"})
    if "ts" not in left.columns:
        left = left.rename(columns={left.columns[0]: "ts"})
    right = h1[["rsi14"]].dropna().reset_index().rename(columns={h1.index.name or "index": "h1_ts"})
    if "h1_ts" not in right.columns:
        right = right.rename(columns={right.columns[0]: "h1_ts"})
    ctx = pd.merge_asof(
        left.sort_values("ts"), right.sort_values("h1_ts"),
        left_on="ts", right_on="h1_ts", direction="backward", allow_exact_matches=True,
    )
    signals=[]
    for row in ctx.itertuples(index=False):
        ts=pd.Timestamp(row.ts)
        if not (start <= ts < end):
            continue
        vals=(row.close,row.sma21,row.sma50,row.vol_ratio,row.rsi6,row.atr14,row.rsi14,row.prior_close)
        if not all(math.isfinite(float(v)) for v in vals):
            continue
        vol_ok=float(row.vol_ratio)>base.VOL_MIN_RATIO
        side=0; method=""
        if bool(row.cross_up_sma21) and vol_ok and float(row.rsi14)<70:
            side=1; method="standard_long"
        elif (
            bool(row.cross_up_rsi23)
            and abs(float(row.close)-float(row.sma21)) > base.SPECIAL_DIST_ATR*float(row.atr14)
            and float(row.close) > float(row.prior_close)
        ):
            side=1; method="special_long"
        if side==0 and bool(row.cross_dn_rsi68) and vol_ok:
            side=-1; method="rsi_short"
        if side==0 and bool(row.cross_dn_sma21) and vol_ok and float(row.rsi14)>30:
            side=-1; method="trend_short"
        if side==0 and bool(row.two_green):
            side=1; method="two_green"
        if side==0 and bool(row.two_red):
            side=-1; method="two_red"
        if side==0:
            continue
        entry_ts=ts+pd.Timedelta(minutes=1)
        if entry_ts not in panel.index:
            continue
        entry=float(panel.loc[entry_ts,"perp_open"]); atr=float(row.atr14)
        if method=="special_long":
            stop=float(row.low)
        elif method in ("two_green","two_red"):
            if ts not in m15.index:
                continue
            child=m15.loc[ts]
            stop=float(child["low"] if side>0 else child["high"])
        else:
            stop=entry-side*base.SL_ATR*atr
        risk=side*(entry-stop)
        if not (entry>0 and stop>0 and risk>0):
            continue
        target=entry+side*base.TP_ATR*atr
        signals.append(base.Signal(
            symbol,ts,entry_ts,side,method,entry,stop,target,atr,
            float(row.rsi6),float(row.rsi14),float(row.vol_ratio),
            float(row.close),float(row.sma21),float(row.sma50),
        ))
    return signals,h4,h1


if __name__ == "__main__":
    base.detect = detect
    base.main()
