#!/usr/bin/env python3
"""Causal public-core reconstruction of INFINITYTRADER Scalping Strategy v2.

External protected TradingView description gives enough detail to reconstruct
its entry families without guessing hidden source code.  This diagnostic freezes
those public rules and deliberately uses stricter real-world path semantics than
TradingView's idealized close-only fills:

Public core reproduced:
- completed higher timeframe = 4h (default), one new decision per 4h close;
- SMA21/SMA50, volume > 0.70 * 20-bar average, RSI6, ATR14 on 4h;
- completed 1h RSI14 as trend/extreme filter;
- Standard long: close crosses above SMA21 + volume + 1h RSI14 < 70;
- Special long: RSI6 crosses above 23 + |close-SMA21| > 1.5 ATR + close>prior close;
- RSI short: RSI6 crosses below 68 + volume support;
- Trend short: close crosses below SMA21 + volume + 1h RSI14 > 30;
- Method4: two consecutive green 4h bars -> long; two red -> short;
- default generic SL=1.5 ATR / TP=4 ATR;
- special-long SL uses signal 4h low;
- method4 tight SL uses final completed 15m child low/high;
- manual exit: long if SMA21 crosses below SMA50 or completed 1h RSI14 >68;
  short if SMA21 crosses above SMA50 or 1h RSI14 <25.

Unknown/ambiguous closed-source features (loss-state reentry and exact trailing
lock implementation) are NOT invented.  We first test whether the published
core possesses after-cost geometry.  If it does, only then is hidden-management
reconstruction worth spending holdout data on.

Execution diagnostic:
- observe only completed bars;
- entry strictly next 1m open;
- fixed stop/target are active intraminute with stop-first ordering if both hit;
- manual exit is observed on completed 4h/1h state and fills next minute;
- current project RT hurdle = 21bp;
- no account, portfolio, leverage, sizing, or matching engine is created here.
Formal success is NautilusTrader-only.
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

PROJECT_COST = 0.0021
SMA_FAST = 21
SMA_SLOW = 50
VOL_PERIOD = 20
VOL_MIN_RATIO = 0.70
RSI_FAST = 6
RSI_FILTER = 14
ATR_PERIOD = 14
SL_ATR = 1.5
TP_ATR = 4.0
SPECIAL_DIST_ATR = 1.5


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    event_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    side: int
    method: str
    entry: float
    stop: float
    target: float
    atr: float
    rsi6: float
    rsi1h14: float
    vol_ratio: float
    close: float
    sma21: float
    sma50: float


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    method: str
    event_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    side: int
    entry: float
    stop: float
    target: float
    exit_price: float
    exit_reason: str
    gross_return: float
    net_return: float
    net_r: float
    holding_minutes: int


def rma(series: pd.Series, length: int) -> pd.Series:
    v=pd.to_numeric(series,errors="coerce").to_numpy(dtype=float)
    out=np.full(len(v),np.nan)
    if len(v)<length: return pd.Series(out,index=series.index)
    first=float(np.nanmean(v[:length])); out[length-1]=first; prev=first; a=1.0/length
    for i in range(length,len(v)):
        x=v[i]
        if math.isfinite(x): prev=a*x+(1-a)*prev
        out[i]=prev
    return pd.Series(out,index=series.index)


def rsi(close: pd.Series, length: int) -> pd.Series:
    d=close.diff(); up=d.clip(lower=0).fillna(0); dn=(-d.clip(upper=0)).fillna(0)
    au=rma(up,length); ad=rma(dn,length); rs=au/ad.replace(0,np.nan)
    x=100-100/(1+rs); x=x.where(ad.ne(0),100.0); x=x.where(au.ne(0)|ad.ne(0),50.0); return x


def bars(panel: pd.DataFrame, minutes: int) -> pd.DataFrame:
    g=panel.resample(f"{minutes}min",label="left",closed="left")
    x=pd.DataFrame({
        "open":g["perp_open"].first(),"high":g["perp_high"].max(),"low":g["perp_low"].min(),
        "close":g["perp_close"].last(),"volume":g["perp_quote_volume"].sum(),"count":g["perp_close"].count(),
    })
    x=x[x["count"].eq(minutes)].copy(); x.index=x.index+pd.Timedelta(minutes=minutes-1); x["completed_ts"]=x.index
    return x


def indicators(panel: pd.DataFrame):
    h4=bars(panel,240); h1=bars(panel,60); m15=bars(panel,15)
    h4["sma21"]=h4["close"].rolling(SMA_FAST).mean(); h4["sma50"]=h4["close"].rolling(SMA_SLOW).mean()
    h4["vol_ma20"]=h4["volume"].rolling(VOL_PERIOD).mean(); h4["vol_ratio"]=h4["volume"]/h4["vol_ma20"].replace(0,np.nan)
    h4["rsi6"]=rsi(h4["close"],RSI_FAST)
    pc=h4["close"].shift(1); tr=pd.concat([h4["high"]-h4["low"],(h4["high"]-pc).abs(),(h4["low"]-pc).abs()],axis=1).max(axis=1)
    h4["atr14"]=rma(tr,ATR_PERIOD)
    h4["cross_up_sma21"]=h4["close"].gt(h4["sma21"]) & h4["close"].shift(1).le(h4["sma21"].shift(1))
    h4["cross_dn_sma21"]=h4["close"].lt(h4["sma21"]) & h4["close"].shift(1).ge(h4["sma21"].shift(1))
    h4["cross_up_rsi23"]=h4["rsi6"].gt(23) & h4["rsi6"].shift(1).le(23)
    h4["cross_dn_rsi68"]=h4["rsi6"].lt(68) & h4["rsi6"].shift(1).ge(68)
    h4["cross_dn_ma"]=h4["sma21"].lt(h4["sma50"]) & h4["sma21"].shift(1).ge(h4["sma50"].shift(1))
    h4["cross_up_ma"]=h4["sma21"].gt(h4["sma50"]) & h4["sma21"].shift(1).le(h4["sma50"].shift(1))
    h4["green"]=h4["close"].gt(h4["open"]); h4["red"]=h4["close"].lt(h4["open"])
    h4["two_green"]=h4["green"] & h4["green"].shift(1).fillna(False)
    h4["two_red"]=h4["red"] & h4["red"].shift(1).fillna(False)
    h1["rsi14"]=rsi(h1["close"],RSI_FILTER)
    return h4,h1,m15


def detect(symbol: str,panel: pd.DataFrame,year:int):
    h4,h1,m15=indicators(panel); start=pd.Timestamp(f"{year}-01-01",tz="UTC"); end=pd.Timestamp(f"{year+1}-01-01",tz="UTC")
    # Causal completed 1h context onto 4h decision timestamps.
    ctx=pd.merge_asof(
        h4.reset_index().rename(columns={h4.index.name or "index":"ts"}).sort_values("ts"),
        h1[["rsi14"]].dropna().reset_index().rename(columns={h1.index.name or "index":"h1_ts"}).sort_values("h1_ts"),
        left_on="ts",right_on="h1_ts",direction="backward",allow_exact_matches=True,
    )
    signals=[]
    for row in ctx.itertuples(index=False):
        ts=pd.Timestamp(row.ts)
        if not (start<=ts<end): continue
        vals=(row.close,row.sma21,row.sma50,row.vol_ratio,row.rsi6,row.atr14,row.rsi14)
        if not all(math.isfinite(float(v)) for v in vals): continue
        vol_ok=float(row.vol_ratio)>VOL_MIN_RATIO; side=0; method=""
        # Fixed priority is external-description order; one decision per 4h close.
        if bool(row.cross_up_sma21) and vol_ok and float(row.rsi14)<70:
            side=1; method="standard_long"
        elif bool(row.cross_up_rsi23) and abs(float(row.close)-float(row.sma21))>SPECIAL_DIST_ATR*float(row.atr14) and float(row.close)>float(row._asdict().get('close',row.close)):
            # prior-close condition evaluated below using h4 lookup to avoid tuple ambiguity
            prev=h4.loc[:ts].iloc[-2] if len(h4.loc[:ts])>=2 else None
            if prev is not None and float(row.close)>float(prev["close"]): side=1; method="special_long"
        if side==0 and bool(row.cross_dn_rsi68) and vol_ok:
            side=-1; method="rsi_short"
        if side==0 and bool(row.cross_dn_sma21) and vol_ok and float(row.rsi14)>30:
            side=-1; method="trend_short"
        if side==0 and bool(row.two_green): side=1; method="two_green"
        if side==0 and bool(row.two_red): side=-1; method="two_red"
        if side==0: continue
        entry_ts=ts+pd.Timedelta(minutes=1)
        if entry_ts not in panel.index: continue
        entry=float(panel.loc[entry_ts,"perp_open"]); atr=float(row.atr14)
        if method=="special_long": stop=float(row.low)
        elif method in ("two_green","two_red"):
            known=m15[m15.index<=ts]
            if known.empty: continue
            c=known.iloc[-1]; stop=float(c["low"] if side>0 else c["high"])
        else: stop=entry-side*SL_ATR*atr
        risk=side*(entry-stop)
        if not (entry>0 and stop>0 and risk>0): continue
        target=entry+side*TP_ATR*atr
        signals.append(Signal(symbol,ts,entry_ts,side,method,entry,stop,target,atr,float(row.rsi6),float(row.rsi14),float(row.vol_ratio),float(row.close),float(row.sma21),float(row.sma50)))
    return signals,h4,h1


def manual_exit_schedule(sig:Signal,h4:pd.DataFrame,h1:pd.DataFrame):
    candidates=[]
    later4=h4[h4.index>sig.event_ts]
    if sig.side>0:
        x=later4[later4["cross_dn_ma"]]
    else:
        x=later4[later4["cross_up_ma"]]
    if not x.empty: candidates.append(pd.Timestamp(x.index[0])+pd.Timedelta(minutes=1))
    later1=h1[h1.index>sig.event_ts]
    if sig.side>0: y=later1[later1["rsi14"]>68]
    else: y=later1[later1["rsi14"]<25]
    if not y.empty: candidates.append(pd.Timestamp(y.index[0])+pd.Timedelta(minutes=1))
    return min(candidates) if candidates else None


def score(sig:Signal,panel:pd.DataFrame,h4:pd.DataFrame,h1:pd.DataFrame):
    manual=manual_exit_schedule(sig,h4,h1); cursor=sig.entry_ts; last=panel.index[-1]
    while cursor<=last:
        if cursor not in panel.index: cursor+=pd.Timedelta(minutes=1); continue
        r=panel.loc[cursor]
        if manual is not None and cursor>=manual:
            px=float(r["perp_open"]); reason="manual"; break
        hi=float(r["perp_high"]); lo=float(r["perp_low"])
        stop=(lo<=sig.stop) if sig.side>0 else (hi>=sig.stop); target=(hi>=sig.target) if sig.side>0 else (lo<=sig.target)
        if stop: px=sig.stop; reason="stop"; break
        if target: px=sig.target; reason="target"; break
        cursor+=pd.Timedelta(minutes=1)
    else: return None
    gross=sig.side*(px/sig.entry-1); net=gross-PROJECT_COST; planned=abs(sig.entry-sig.stop)/sig.entry+PROJECT_COST
    return Trade(sig.symbol,sig.method,sig.event_ts,sig.entry_ts,cursor,sig.side,sig.entry,sig.stop,sig.target,float(px),reason,gross,net,net/planned if planned>0 else math.nan,int((cursor-sig.entry_ts).total_seconds()//60))


def summary(trades,days):
    if not trades: return {"trades":0,"win_rate":0.0,"pf":0.0,"mean_net":0.0,"mean_net_r":0.0,"trades_per_day":0.0}
    v=np.array([x.net_return for x in trades]); g=v[v>0].sum(); l=-v[v<0].sum()
    return {"trades":len(trades),"win_rate":float((v>0).mean()),"pf":float(g/l) if l>0 else 999999.0,"mean_net":float(v.mean()),"mean_net_r":float(np.mean([x.net_r for x in trades])),"trades_per_day":len(trades)/days,"target_rate":sum(x.exit_reason=="target" for x in trades)/len(trades),"stop_rate":sum(x.exit_reason=="stop" for x in trades)/len(trades)}


def global_arbitrate(trades,year):
    out=[]; free=pd.Timestamp(f"{year}-01-01",tz="UTC")
    for t in sorted(trades,key=lambda x:(x.entry_ts,x.symbol)):
        if t.entry_ts<free: continue
        out.append(t); free=t.exit_ts+pd.Timedelta(minutes=1)
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,default=2025); ap.add_argument("--output",type=Path,required=True); ap.add_argument("--cache",type=Path,default=Path(".cache/c53-infinity")); a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    days=365 if a.year%4 else 366; all_trades=[]; per={}
    for s in SYMBOLS:
        p=load_symbol(s,a.year,a.cache); sigs,h4,h1=detect(s,p,a.year); trades=[]; free=pd.Timestamp(f"{a.year}-01-01",tz="UTC")
        for z in sigs:
            if z.entry_ts<free: continue
            t=score(z,p,h4,h1)
            if t is None: continue
            trades.append(t); all_trades.append(t); free=t.exit_ts+pd.Timedelta(minutes=1)
        per[s]={"signals":len(sigs),"summary":summary(trades,days),"by_method":{m:summary([t for t in trades if t.method==m],days) for m in sorted(set(t.method for t in trades))}}
        pd.DataFrame([asdict(t) for t in trades]).to_csv(a.output/f"{s}_trades.csv",index=False)
    glob=global_arbitrate(all_trades,a.year); pd.DataFrame([asdict(t) for t in glob]).to_csv(a.output/"global_trades.csv",index=False)
    result={"study":"INFINITYTRADER public-core causal replication","year":a.year,"cost_rt":PROJECT_COST,"per_symbol":per,"global_one_position":summary(glob,days),"omitted_unknowns":["loss-state reentry","exact 6ATR/1ATR trailing-lock implementation"]}
    (a.output/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); print(json.dumps(result,indent=2,sort_keys=True))

if __name__=="__main__": main()
