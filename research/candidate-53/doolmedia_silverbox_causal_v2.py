#!/usr/bin/env python3
"""Causal asynchronous interpretation of the public DoolMedia Silver-Box rules.

The first reconstruction required the 5m RSI cross to land on the final 5m
child of the same 15m bar.  That is causal but may be stricter than the stated
"15 min chart as master; 5 min for fine entry" intent.  This independent v2
implements the natural live interpretation without changing any thresholds:

- maintain the latest fully completed 15m RSI(14)/ATR(14) master state;
- on every fully completed 5m bar, if the latest master is oversold/overbought
  and RSI(6) makes the published cross, confirm the fine entry;
- enter strictly next minute open;
- the "entry candle" is the confirming 5m candle, so stop is 1 master ATR beyond
  its low/high; target remains 1.5R or later opposing completed-15m RSI exit;
- weekend flat and conservative 1m stop-before-target ordering are unchanged.

No outcome-derived thresholds are introduced. This is a semantic replication
check; formal success still requires NautilusTrader.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path

import pandas as pd

from doolmedia_silverbox_causal_study import (
    SYMBOLS, LONG_MASTER, SHORT_MASTER, LONG_FINE_CROSS, SHORT_FINE_CROSS,
    ATR_MULT, TP_R, Signal, detect as detect_v1, with_indicators, score, summarize,
    arbitrate, load_symbol,
)


def detect_async(symbol: str, panel: pd.DataFrame, year: int):
    f5, f15 = with_indicators(panel)
    start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    signals=[]
    master = f15[["rsi14","atr14"]].dropna().sort_index()
    if master.empty:
        return signals, f15
    for ts, fine in f5.loc[(f5.index >= start) & (f5.index < end)].iterrows():
        if ts.dayofweek >= 5:
            continue
        known = master[master.index <= ts]
        if known.empty:
            continue
        m = known.iloc[-1]
        rsi15=float(m["rsi14"]); atr=float(m["atr14"]); rsi5=float(fine["rsi6"])
        if not all(math.isfinite(v) for v in (rsi15,atr,rsi5)) or atr<=0:
            continue
        if rsi15 <= LONG_MASTER and bool(fine["cross_up_45"]):
            side=1
        elif rsi15 >= SHORT_MASTER and bool(fine["cross_dn_55"]):
            side=-1
        else:
            continue
        entry_ts=pd.Timestamp(ts)+pd.Timedelta(minutes=1)
        if entry_ts not in panel.index:
            continue
        entry=float(panel.loc[entry_ts,"perp_open"])
        if side>0:
            stop=float(fine["low"])-ATR_MULT*atr
            risk=entry-stop
            target=entry+TP_R*risk
        else:
            stop=float(fine["high"])+ATR_MULT*atr
            risk=stop-entry
            target=entry-TP_R*risk
        if not (entry>0 and stop>0 and target>0 and risk>0):
            continue
        signals.append(Signal(symbol,pd.Timestamp(ts),entry_ts,side,entry,stop,target,rsi15,rsi5,atr))
    return signals,f15


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,default=2025); ap.add_argument("--output",type=Path,required=True); ap.add_argument("--cache",type=Path,default=Path(".cache/c53-doolmedia-v2")); args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    days=365 if args.year%4 else 366
    per_symbol={}; all_scored=[]
    for symbol in SYMBOLS:
        panel=load_symbol(symbol,args.year,args.cache)
        signals,f15=detect_async(symbol,panel,args.year)
        trades=[]; free=pd.Timestamp(f"{args.year}-01-01",tz="UTC")
        for sig in signals:
            if sig.entry_ts<free: continue
            tr=score(sig,panel,f15)
            if tr is None: continue
            trades.append(tr); all_scored.append(tr); free=tr.exit_ts+pd.Timedelta(minutes=1)
        per_symbol[symbol]={"signals":len(signals),"summary":summarize(trades,days)}
        pd.DataFrame([asdict(t) for t in trades]).to_csv(args.output/f"{symbol}_trades.csv",index=False)
    global_trades=arbitrate(all_scored,args.year)
    pd.DataFrame([asdict(t) for t in global_trades]).to_csv(args.output/"global_trades.csv",index=False)
    summary={
        "study":"DoolMedia Silver-Box causal async-master/fine interpretation",
        "year":args.year,
        "semantic_change_only":"latest completed 15m context carried forward to every completed 5m cross; stop anchored to confirming 5m candle",
        "per_symbol":per_symbol,
        "global_one_position":summarize(global_trades,days),
    }
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,indent=2,sort_keys=True))

if __name__=="__main__": main()
