#!/usr/bin/env python3
"""Causal replication of the exact public Pine source for SMC V18.2.

Source provenance:
- TradingView open-source publication: "Stratégie SMC V18.2 (BTC/EUR FINAL R3 - Tendance)".
- A public FMZ mirror exposes the complete Pine v6 source verbatim enough to
  reconstruct every market-decision rule.

The purpose is specifically to distinguish implementation artifacts from
market-logic edge. Two frozen variants run side by side on the same data:

EXACT_SOURCE
  Reproduces the Pine source as written, including two suspicious implementation
  details which can inflate/alter historical behavior:
  1) ``var float sl_level`` and ``var float rr_distance_usd`` are initialized
     inside the mitigation block, so Pine's ``var`` semantics persist their
     first values across later entries rather than recomputing them.
  2) bullish/bearish invalidation booleans are calculated but never used; an
     invalidated OB may therefore still trigger.

REPAIRED_IMPLEMENTATION
  Changes no thresholds and no pattern definitions. It only recomputes stop/risk
  from the current OB at every entry and actually rejects an OB once the source's
  own invalidation condition becomes true. If the external performance survives,
  the market logic deserves promotion; if only EXACT_SOURCE works, the claimed
  edge is an implementation artifact rather than a reusable strategy.

Important: We do NOT silently "correct" the source's unusual BOS/FVG definitions:
- fvg_bullish = high[1] < low[3]
- fvg_bearish = low[1] > high[3]
- bullish BOS condition = close > last_swing_low
- bearish BOS condition = close < last_swing_high
Those are economic logic choices in the source and are preserved in both variants.

Causality/execution diagnostic:
- 15m strategy bars built only from completed 1m Binance USD-M bars;
- H1 EMA200 uses the latest completed H1 bar only (no future H1 value);
- strategy.entry at 15m close fills strictly next 1m open;
- stop/target then walk actual 1m OHLC, stop-first if both touched;
- current project RT hurdle 21bp is charged from each realized return;
- no custom account/matching/portfolio engine; formal proof remains NautilusTrader.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from keltrader_causal_study import SYMBOLS, load_symbol

SWING=4
LIQ_SEARCH=7
LIQ_TOL_PCT=0.1
EMA_LEN=200
MITIGATION_BUFFER_FRAC=0.00005
RT_COST=0.0021
MIN_SL_DISTANCE=0.0001


@dataclass(frozen=True, slots=True)
class Trade:
    variant: str
    symbol: str
    signal_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    side: int
    ob_high: float
    ob_low: float
    entry: float
    stop: float
    target: float
    exit_price: float
    exit_reason: str
    gross_return: float
    net_return: float
    net_r: float
    holding_minutes: int


def aggregate(panel:pd.DataFrame, minutes:int)->pd.DataFrame:
    g=panel.resample(f"{minutes}min",label="left",closed="left")
    x=pd.DataFrame({"open":g["perp_open"].first(),"high":g["perp_high"].max(),"low":g["perp_low"].min(),"close":g["perp_close"].last(),"count":g["perp_close"].count()})
    x=x[x["count"].eq(minutes)].copy(); x.index=x.index+pd.Timedelta(minutes=minutes-1); return x


def ema(s:pd.Series,n:int)->pd.Series:
    return s.ewm(span=n,adjust=False,min_periods=n).mean()


def features(panel:pd.DataFrame)->pd.DataFrame:
    m15=aggregate(panel,15); h1=aggregate(panel,60); h1["ema200"]=ema(h1["close"],EMA_LEN)
    left=m15.reset_index().rename(columns={m15.index.name or "index":"ts"})
    if "ts" not in left: left=left.rename(columns={left.columns[0]:"ts"})
    right=h1[["ema200"]].dropna().reset_index().rename(columns={h1.index.name or "index":"h1_ts"})
    if "h1_ts" not in right: right=right.rename(columns={right.columns[0]:"h1_ts"})
    x=pd.merge_asof(left.sort_values("ts"),right.sort_values("h1_ts"),left_on="ts",right_on="h1_ts",direction="backward",allow_exact_matches=True).set_index("ts")
    # Pine rolling primitives. These are computed on completed 15m bars only.
    x["highest9"]=x["high"].rolling(SWING*2+1,min_periods=1).max(); x["lowest9"]=x["low"].rolling(SWING*2+1,min_periods=1).min()
    x["new_high_event"]=x["high"].eq(x["highest9"]); x["new_low_event"]=x["low"].eq(x["lowest9"])
    # ta.barssince(condition)==4: exactly four bars since most recent true.
    last_hi=np.full(len(x),-10_000,dtype=int); last_lo=np.full(len(x),-10_000,dtype=int); ph=-10_000; pl=-10_000
    for i,(a,b) in enumerate(zip(x["new_high_event"].to_numpy(),x["new_low_event"].to_numpy())):
        if bool(a): ph=i
        if bool(b): pl=i
        last_hi[i]=ph; last_lo[i]=pl
    idx=np.arange(len(x)); x["sh_confirmed"]=(idx-last_hi)==SWING; x["sl_confirmed"]=(idx-last_lo)==SWING
    x["liq_lowest7"]=x["low"].rolling(LIQ_SEARCH,min_periods=1).min(); x["liq_highest7"]=x["high"].rolling(LIQ_SEARCH,min_periods=1).max()
    return x


def score_trade(variant,symbol,side,signal_ts,entry_ts,entry,stop,target,ob_high,ob_low,panel):
    cursor=entry_ts; last=panel.index[-1]
    while cursor<=last:
        if cursor not in panel.index: cursor+=pd.Timedelta(minutes=1); continue
        r=panel.loc[cursor]; hi=float(r["perp_high"]); lo=float(r["perp_low"])
        stop_hit=lo<=stop if side>0 else hi>=stop; target_hit=hi>=target if side>0 else lo<=target
        if stop_hit: px=stop; reason="stop"; break
        if target_hit: px=target; reason="target"; break
        cursor+=pd.Timedelta(minutes=1)
    else: return None
    gross=side*(px/entry-1); net=gross-RT_COST; planned=abs(entry-stop)/entry+RT_COST
    return Trade(variant,symbol,signal_ts,entry_ts,cursor,side,ob_high,ob_low,entry,stop,target,float(px),reason,gross,net,net/planned if planned>0 else math.nan,int((cursor-entry_ts).total_seconds()//60))


def run_variant(symbol,panel,year,variant):
    x=features(panel); start=pd.Timestamp(f"{year}-01-01",tz="UTC"); end=pd.Timestamp(f"{year+1}-01-01",tz="UTC")
    # Pine var state.
    ob_high=math.nan; ob_low=math.nan; ob_bull=False
    last_swing_high=math.nan; last_swing_low=math.nan
    stale_sl=math.nan; stale_rr=math.nan
    active_trade=None; trades=[]; free_at=start
    # Warm through all available loader bars so EMA/swing state enters year causally.
    rows=list(x.itertuples())
    for i,row in enumerate(rows):
        ts=pd.Timestamp(row.Index)
        # initialize/update swings exactly from source bar references
        if not math.isfinite(last_swing_high):
            past=x.iloc[max(0,i-199):i+1]; last_swing_high=float(past["high"].max())
        if not math.isfinite(last_swing_low):
            past=x.iloc[max(0,i-199):i+1]; last_swing_low=float(past["low"].min())
        if bool(row.sh_confirmed) and i>=SWING: last_swing_high=float(rows[i-SWING].high)
        if bool(row.sl_confirmed) and i>=SWING: last_swing_low=float(rows[i-SWING].low)
        fib=(last_swing_high+last_swing_low)/2.0
        if i<3: continue
        prev=rows[i-1]; older=rows[i-3]
        fvg_bull=float(prev.high)<float(older.low)
        fvg_bear=float(prev.low)>float(older.high)
        tol=float(row.close)*LIQ_TOL_PCT/100.0
        has_liq_bull=float(row.liq_lowest7)<float(row.low)-tol
        has_liq_bear=float(row.liq_highest7)>float(row.high)+tol
        bullish=(float(prev.close)<float(prev.open) and fvg_bull and float(row.close)>last_swing_low and has_liq_bull and float(row.close)<fib)
        bearish=(float(prev.close)>float(prev.open) and fvg_bear and float(row.close)<last_swing_high and has_liq_bear and float(row.close)>fib)

        # We model the source's `strategy.position_size == 0` from known scored
        # trade interval. No new OB overwrite while the position is open.
        flat = ts >= free_at
        if (not math.isfinite(ob_high)) or flat:
            if bullish or bearish:
                ob_bull=bool(bullish)
                ob_high=float(prev.high); ob_low=float(prev.low)

        if not math.isfinite(ob_high) or not flat or not (start<=ts<end):
            continue
        buffer=MITIGATION_BUFFER_FRAC*float(row.close)
        touched=(float(row.low)<=ob_high+buffer) if ob_bull else (float(row.high)>=ob_low-buffer)
        invalid=(ob_bull and float(row.close)<ob_low) or ((not ob_bull) and float(row.close)>ob_high)
        if variant=="REPAIRED_IMPLEMENTATION" and invalid:
            ob_high=math.nan; ob_low=math.nan
            continue
        if not touched:
            continue

        current_sl=ob_low if ob_bull else ob_high
        current_rr=abs(float(row.close)-current_sl)
        if variant=="EXACT_SOURCE":
            if not math.isfinite(stale_sl): stale_sl=current_sl
            if not math.isfinite(stale_rr): stale_rr=current_rr
            sl=stale_sl; rr=max(stale_rr,MIN_SL_DISTANCE)
        else:
            sl=current_sl; rr=max(current_rr,MIN_SL_DISTANCE)
        side=1 if ob_bull else -1
        # Source signal is generated at 15m close. Causal market entry fills next minute open.
        entry_ts=ts+pd.Timedelta(minutes=1)
        if entry_ts not in panel.index: continue
        # Trend filter is evaluated at signal close, like source.
        if not math.isfinite(float(row.ema200)): continue
        if side>0 and not (float(row.close)>float(row.ema200)): continue
        if side<0 and not (float(row.close)<float(row.ema200)): continue
        entry=float(panel.loc[entry_ts,"perp_open"])
        # Pine source computes TP from signal close, not eventual next-bar fill.
        target=float(row.close)+side*rr*3.0
        # Ensure geometry can actually bracket the fill; exact source may fail here.
        if side*(entry-sl)<=0 or side*(target-entry)<=0:
            # This is a real consequence of stale local-var state; no invented repair.
            continue
        tr=score_trade(variant,symbol,side,ts,entry_ts,entry,sl,target,ob_high,ob_low,panel)
        if tr is None: continue
        trades.append(tr); free_at=tr.exit_ts+pd.Timedelta(minutes=1)
    return trades


def summarize(trades,days):
    if not trades:return {"trades":0,"win_rate":0.0,"pf":0.0,"mean_net":0.0,"mean_net_r":0.0,"trades_per_day":0.0}
    v=np.array([t.net_return for t in trades]); g=v[v>0].sum(); l=-v[v<0].sum()
    return {"trades":len(trades),"win_rate":float((v>0).mean()),"pf":float(g/l) if l>0 else 999999.0,"mean_net":float(v.mean()),"mean_net_r":float(np.mean([t.net_r for t in trades])),"trades_per_day":len(trades)/days,"target_rate":sum(t.exit_reason=="target" for t in trades)/len(trades),"stop_rate":sum(t.exit_reason=="stop" for t in trades)/len(trades)}


def global_arbitrate(trades,year):
    out=[]; free=pd.Timestamp(f"{year}-01-01",tz="UTC")
    for t in sorted(trades,key=lambda z:(z.entry_ts,z.symbol,z.variant)):
        if t.entry_ts<free:continue
        out.append(t); free=t.exit_ts+pd.Timedelta(minutes=1)
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,default=2025); ap.add_argument("--output",type=Path,required=True); ap.add_argument("--cache",type=Path,default=Path(".cache/c53-rico")); a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    days=365 if a.year%4 else 366; result={"study":"SMC V18.2 exact-source vs implementation-repaired causal replication","year":a.year,"cost_rt":RT_COST,"variants":{}}
    for variant in ("EXACT_SOURCE","REPAIRED_IMPLEMENTATION"):
        all_t=[]; per={}
        for s in SYMBOLS:
            p=load_symbol(s,a.year,a.cache); t=run_variant(s,p,a.year,variant); all_t.extend(t); per[s]=summarize(t,days); pd.DataFrame([asdict(z) for z in t]).to_csv(a.output/f"{variant}_{s}.csv",index=False)
        glob=global_arbitrate(all_t,a.year); pd.DataFrame([asdict(z) for z in glob]).to_csv(a.output/f"{variant}_global.csv",index=False)
        result["variants"][variant]={"per_symbol":per,"global_one_position":summarize(glob,days)}
    (a.output/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); print(json.dumps(result,indent=2,sort_keys=True))

if __name__=="__main__":main()
