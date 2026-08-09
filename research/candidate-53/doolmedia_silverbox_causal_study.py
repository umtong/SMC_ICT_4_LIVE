#!/usr/bin/env python3
"""Causal reconstruction of the public DoolMedia Silver-Box Reversion rules.

External public strategy statement (TradingView, 2026-05-22):
- universe includes BTCUSDT, ETHUSDT, SOLUSDT (plus XAUUSD, excluded here)
- 15m master / 5m fine entry
- long: RSI(14,15m) <=35 and RSI(6,5m) crosses above 45
- short: RSI(14,15m) >=70 and RSI(6,5m) crosses below 55
- SL: 1 ATR(14) beyond entry-candle low/high
- TP: 1.5R OR opposing RSI level
- crypto weekend-flat
- claimed 1433 trades, 92.11% winners, PF 2.59 under 5bp RT fee and no slippage.

This file is an opportunity/geometry diagnostic, not an account backtester. It
uses the already-solved checksum-verified Candidate53 monthly Binance loader and
never creates portfolio/account/execution infrastructure. Any promising result
must be promoted into NautilusTrader for formal evaluation.

Causality choices are deliberately conservative and frozen before results:
- only fully completed 15m and 5m bars are observed;
- the 5m cross must occur on the final completed 5m child of that 15m master bar;
- entry is the strictly next 1m open;
- TradingView ta.rsi/ta.atr semantics are reproduced with Wilder RMA;
- "entry candle" means the completed 15m master signal candle;
- opposing-RSI exit is known only after a completed 15m bar and fills next minute;
- if SL and TP are both touched in one 1m bar, SL wins (conservative ordering);
- current project hurdle is 21bp RT. Claimed 5bp RT is also shown only as a
  reproduction diagnostic, never as project success evidence.
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
CLAIMED_COST = 0.0005
LONG_MASTER = 35.0
SHORT_MASTER = 70.0
LONG_FINE_CROSS = 45.0
SHORT_FINE_CROSS = 55.0
ATR_MULT = 1.0
TP_R = 1.5


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    signal_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    side: int
    entry: float
    stop: float
    target: float
    rsi15: float
    rsi5: float
    atr15: float


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    signal_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    side: int
    entry: float
    stop: float
    target: float
    exit_price: float
    exit_reason: str
    gross_return: float
    net_return_21bp: float
    net_return_5bp: float
    net_r_21bp: float
    holding_minutes: int


def rma(series: pd.Series, length: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return pd.Series(out, index=series.index)
    # TradingView Wilder RMA starts from SMA of first length observations.
    first = np.nanmean(values[:length])
    out[length - 1] = first
    alpha = 1.0 / length
    prev = first
    for i in range(length, len(values)):
        x = values[i]
        if not math.isfinite(x):
            out[i] = prev
            continue
        prev = alpha * x + (1.0 - alpha) * prev
        out[i] = prev
    return pd.Series(out, index=series.index)


def rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0).fillna(0.0)
    down = (-delta.clip(upper=0.0)).fillna(0.0)
    avg_up = rma(up, length)
    avg_down = rma(down, length)
    rs = avg_up / avg_down.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + rs)
    result = result.where(avg_down.ne(0.0), 100.0)
    result = result.where(avg_up.ne(0.0) | avg_down.ne(0.0), 50.0)
    return result


def aggregate(panel: pd.DataFrame, minutes: int) -> pd.DataFrame:
    grouped = panel.resample(f"{minutes}min", label="left", closed="left")
    out = pd.DataFrame({
        "open": grouped["perp_open"].first(),
        "high": grouped["perp_high"].max(),
        "low": grouped["perp_low"].min(),
        "close": grouped["perp_close"].last(),
        "volume": grouped["perp_quote_volume"].sum(),
        "count": grouped["perp_close"].count(),
    })
    out = out[out["count"].eq(minutes)].copy()
    # Completion timestamp = last minute open in interval.
    out.index = out.index + pd.Timedelta(minutes=minutes - 1)
    out["completed_ts"] = out.index
    return out


def with_indicators(panel: pd.DataFrame):
    f5 = aggregate(panel, 5)
    f15 = aggregate(panel, 15)
    f5["rsi6"] = rsi(f5["close"], 6)
    f5["cross_up_45"] = f5["rsi6"].gt(LONG_FINE_CROSS) & f5["rsi6"].shift(1).le(LONG_FINE_CROSS)
    f5["cross_dn_55"] = f5["rsi6"].lt(SHORT_FINE_CROSS) & f5["rsi6"].shift(1).ge(SHORT_FINE_CROSS)
    f15["rsi14"] = rsi(f15["close"], 14)
    prev = f15["close"].shift(1)
    tr = pd.concat([
        f15["high"] - f15["low"],
        (f15["high"] - prev).abs(),
        (f15["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    f15["atr14"] = rma(tr, 14)
    return f5, f15


def detect(symbol: str, panel: pd.DataFrame, year: int) -> tuple[list[Signal], pd.DataFrame]:
    f5, f15 = with_indicators(panel)
    start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    signals=[]
    # A completed 15m bar at hh:mm:14 aligns to completed 5m child hh:mm:14.
    for ts, row in f15.loc[(f15.index >= start) & (f15.index < end)].iterrows():
        if ts not in f5.index:
            continue
        fine = f5.loc[ts]
        master = float(row["rsi14"]); fine_rsi = float(fine["rsi6"]); atr=float(row["atr14"])
        if not all(math.isfinite(v) for v in (master, fine_rsi, atr)) or atr <= 0:
            continue
        if ts.dayofweek >= 5:
            continue
        if master <= LONG_MASTER and bool(fine["cross_up_45"]):
            side=1
        elif master >= SHORT_MASTER and bool(fine["cross_dn_55"]):
            side=-1
        else:
            continue
        entry_ts=ts+pd.Timedelta(minutes=1)
        if entry_ts not in panel.index:
            continue
        entry=float(panel.loc[entry_ts,"perp_open"])
        if side>0:
            stop=float(row["low"])-ATR_MULT*atr
            risk=entry-stop
            target=entry+TP_R*risk
        else:
            stop=float(row["high"])+ATR_MULT*atr
            risk=stop-entry
            target=entry-TP_R*risk
        if not (entry>0 and stop>0 and target>0 and risk>0):
            continue
        signals.append(Signal(symbol, pd.Timestamp(ts), entry_ts, side, entry, stop, target, master, fine_rsi, atr))
    return signals, f15


def scheduled_rsi_exit(signal: Signal, f15: pd.DataFrame) -> dict[pd.Timestamp,float]:
    # map next-minute open timestamps after completed master bars that satisfy opposing level
    after=f15[f15.index>signal.signal_ts]
    out={}
    if signal.side>0:
        selected=after[after["rsi14"].ge(SHORT_MASTER)]
    else:
        selected=after[after["rsi14"].le(LONG_MASTER)]
    if not selected.empty:
        ts=pd.Timestamp(selected.index[0])+pd.Timedelta(minutes=1)
        out[ts]=float(selected.iloc[0]["close"])
    return out


def score(signal: Signal, panel: pd.DataFrame, f15: pd.DataFrame) -> Trade | None:
    rsi_exit=scheduled_rsi_exit(signal,f15)
    # Hard weekend-flat at first Saturday 00:00 UTC open if reached.
    cursor=signal.entry_ts
    last_ts=panel.index[-1]
    while cursor<=last_ts:
        if cursor not in panel.index:
            cursor+=pd.Timedelta(minutes=1); continue
        row=panel.loc[cursor]
        # known-at-open exits first
        if cursor in rsi_exit:
            px=float(row["perp_open"]); reason="opposing_rsi"; break
        if cursor.dayofweek==5 and cursor.hour==0 and cursor.minute==0:
            px=float(row["perp_open"]); reason="weekend_flat"; break
        h=float(row["perp_high"]); l=float(row["perp_low"])
        if signal.side>0:
            stop_hit=l<=signal.stop; target_hit=h>=signal.target
        else:
            stop_hit=h>=signal.stop; target_hit=l<=signal.target
        if stop_hit:
            px=signal.stop; reason="stop"; break
        if target_hit:
            px=signal.target; reason="target"; break
        cursor+=pd.Timedelta(minutes=1)
    else:
        return None
    gross=signal.side*(px/signal.entry-1.0)
    net21=gross-PROJECT_COST
    net5=gross-CLAIMED_COST
    stop_dist=abs(signal.entry-signal.stop)/signal.entry
    planned=stop_dist+PROJECT_COST
    net_r=net21/planned if planned>0 else math.nan
    return Trade(
        signal.symbol, signal.signal_ts, signal.entry_ts, cursor, signal.side,
        signal.entry, signal.stop, signal.target, float(px), reason,
        gross, net21, net5, net_r, int((cursor-signal.entry_ts).total_seconds()//60),
    )


def summarize(trades: list[Trade], days: int) -> dict:
    if not trades:
        return {"trades":0,"win_rate_21bp":0.0,"pf_21bp":0.0,"mean_net_21bp":0.0,"mean_net_r":0.0,"trades_per_day":0.0}
    vals=np.array([t.net_return_21bp for t in trades],dtype=float)
    vals5=np.array([t.net_return_5bp for t in trades],dtype=float)
    gains=vals[vals>0].sum(); losses=-vals[vals<0].sum()
    gains5=vals5[vals5>0].sum(); losses5=-vals5[vals5<0].sum()
    return {
        "trades":len(trades),
        "win_rate_21bp":float((vals>0).mean()),
        "pf_21bp":float(gains/losses) if losses>0 else float("inf"),
        "mean_net_21bp":float(vals.mean()),
        "win_rate_claimed_5bp":float((vals5>0).mean()),
        "pf_claimed_5bp":float(gains5/losses5) if losses5>0 else float("inf"),
        "mean_net_r":float(np.mean([t.net_r_21bp for t in trades])),
        "trades_per_day":len(trades)/days,
        "target_rate":sum(t.exit_reason=="target" for t in trades)/len(trades),
        "stop_rate":sum(t.exit_reason=="stop" for t in trades)/len(trades),
        "median_holding_minutes":float(np.median([t.holding_minutes for t in trades])),
    }


def arbitrate(all_trades_by_signal: list[Trade], year: int) -> list[Trade]:
    # Diagnostics were scored independently only to know their deterministic exits.
    # The final diagnostic path obeys one global position: earliest eligible signal
    # when flat, ignore all signals while that chosen trade is open.
    ordered=sorted(all_trades_by_signal,key=lambda t:(t.entry_ts,t.symbol))
    chosen=[]; free_at=pd.Timestamp(f"{year}-01-01",tz="UTC")
    for t in ordered:
        if t.entry_ts < free_at:
            continue
        chosen.append(t); free_at=t.exit_ts+pd.Timedelta(minutes=1)
    return chosen


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--year",type=int,default=2025); ap.add_argument("--output",type=Path,required=True); ap.add_argument("--cache",type=Path,default=Path(".cache/c53-doolmedia")); args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    per_symbol={}; all_scored=[]
    for symbol in SYMBOLS:
        panel=load_symbol(symbol,args.year,args.cache)
        signals,f15=detect(symbol,panel,args.year)
        trades=[]
        # overlapping same-symbol signals are ignored by score-time sequential path
        free=pd.Timestamp(f"{args.year}-01-01",tz="UTC")
        for sig in signals:
            if sig.entry_ts<free: continue
            tr=score(sig,panel,f15)
            if tr is None: continue
            trades.append(tr); all_scored.append(tr); free=tr.exit_ts+pd.Timedelta(minutes=1)
        per_symbol[symbol]={"signals":len(signals),"summary":summarize(trades,365 if args.year%4 else 366)}
        pd.DataFrame([asdict(t) for t in trades]).to_csv(args.output/f"{symbol}_trades.csv",index=False)
    global_trades=arbitrate(all_scored,args.year)
    pd.DataFrame([asdict(t) for t in global_trades]).to_csv(args.output/"global_trades.csv",index=False)
    summary={
        "study":"DoolMedia Silver-Box causal public-rule replication",
        "year":args.year,
        "cost_project_rt":PROJECT_COST,
        "cost_claimed_rt":CLAIMED_COST,
        "causal_contract":"completed 15m master + final completed 5m child; next-minute entry; 1m conservative path",
        "per_symbol":per_symbol,
        "global_one_position":summarize(global_trades,365 if args.year%4 else 366),
    }
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,indent=2,sort_keys=True))

if __name__=="__main__": main()
