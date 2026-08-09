#!/usr/bin/env python3
"""Candidate 53 adaptation of Freqtrade Strategy005's exhaustion mechanism.

The public source contributes only the state detector: on 5m bars, volume >4x
its 150-bar mean, close below SMA40, RSI in the low band implied jointly by
RSI>26 and normalized inverse-Fisher-RSI<5, and fast stochastic D>K. Candidate
53 does not copy the source's 10% emergency stop or profit-only exit.

A source event is treated as potential capitulation, not as permission to catch
a falling knife.  A strictly later 5m bar must close back above the signal close
with an up body. Entry is at that completed confirmation close, invalidation is
the signal-bar low, and the past-known signal-time SMA40 is the balance/value
objective. Only geometry with >=1.5 after-cost R survives. One-minute future
paths are descriptive evidence with stop-before-target ordering and the current
21 bp fee+slippage budget. NautilusTrader remains the only execution/accounting
engine if promoted.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive, StudyError, download_verified, read_kline

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COST_RATE = 0.0021
VOLUME_MEAN_BARS = 150
VOLUME_MULTIPLIER = 4.0
RSI_PERIOD = 14
RSI_FLOOR = 26.0
FISHER_NORMA_CEILING = 5.0
SMA_PERIOD = 40
FASTK_PERIOD = 5
FASTD_PERIOD = 3
CONFIRM_BARS = 3
MAX_HOLD_MINUTES = 180
MIN_OBJECTIVE_NET_R = 1.50
GLOBAL_CLUSTER_MINUTES = 3
SYMBOL_DECLUSTER_MINUTES = 30


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    signal_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    entry: float
    stop: float
    target: float
    signal_close: float
    signal_low: float
    sma40: float
    rsi: float
    fisher_rsi_norma: float
    fastk: float
    fastd: float
    volume_ratio: float
    planned_loss_rate: float
    objective_net_r: float
    score: float


@dataclass(frozen=True, slots=True)
class Scored:
    candidate: Candidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def _labels(start: date, end: date) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, end, freq="D")]


def load_symbol(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    frames = []
    for label in _labels(start, end):
        path = download_verified(Archive("um", "daily", "klines", symbol, label, "1m"), cache / symbol)
        frames.append(read_kline(path, prefix="perp"))
    panel = pd.concat(frames, ignore_index=True)
    panel["minute"] = pd.to_datetime(panel["minute"], utc=True, errors="raise")
    panel = panel.sort_values("minute", kind="stable").drop_duplicates("minute", keep="last")
    if panel["minute"].duplicated().any() or not panel["minute"].is_monotonic_increasing:
        raise StudyError(f"invalid minute clock: {symbol}")
    return panel.set_index("minute", drop=False)


def _rsi(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def aggregate_five(panel: pd.DataFrame) -> pd.DataFrame:
    grouped = panel.resample("5min", label="left", closed="left")
    bars = pd.DataFrame({
        "open": grouped["perp_open"].first(),
        "high": grouped["perp_high"].max(),
        "low": grouped["perp_low"].min(),
        "close": grouped["perp_close"].last(),
        "volume": grouped["perp_volume"].sum(),
        "minute_count": grouped["perp_close"].count(),
    })
    bars = bars[bars["minute_count"] == 5].copy()
    bars.index = (bars.index + pd.Timedelta(minutes=4)).as_unit("ns")
    bars["minute"] = bars.index
    bars["sma40"] = bars["close"].rolling(SMA_PERIOD, min_periods=SMA_PERIOD).mean()
    bars["rsi"] = _rsi(bars["close"])
    rsi_scaled = 0.1 * (bars["rsi"] - 50.0)
    fisher = (np.exp(2.0 * rsi_scaled) - 1.0) / (np.exp(2.0 * rsi_scaled) + 1.0)
    bars["fisher_rsi_norma"] = 50.0 * (fisher + 1.0)
    lowest = bars["low"].rolling(FASTK_PERIOD, min_periods=FASTK_PERIOD).min()
    highest = bars["high"].rolling(FASTK_PERIOD, min_periods=FASTK_PERIOD).max()
    bars["fastk"] = 100.0 * (bars["close"] - lowest) / (highest - lowest).replace(0.0, np.nan)
    bars["fastd"] = bars["fastk"].rolling(FASTD_PERIOD, min_periods=FASTD_PERIOD).mean()
    bars["volume_mean"] = bars["volume"].rolling(VOLUME_MEAN_BARS, min_periods=VOLUME_MEAN_BARS).mean()
    return bars


def _geometry(entry: float, stop: float, target: float) -> tuple[float, float]:
    risk = (entry - stop) / entry
    reward = (target - entry) / entry
    if risk <= 0.0 or reward <= 0.0:
        return math.nan, math.nan
    planned_loss = risk + COST_RATE
    return planned_loss, (reward - COST_RATE) / planned_loss


def detect(symbol: str, bars: pd.DataFrame, start: date) -> list[Candidate]:
    result = []
    last_signal: pd.Timestamp | None = None
    for i in range(VOLUME_MEAN_BARS + 10, len(bars) - CONFIRM_BARS):
        row = bars.iloc[i]
        values = [row[k] for k in ("close", "low", "volume", "volume_mean", "sma40", "rsi", "fisher_rsi_norma", "fastd", "fastk")]
        if not all(math.isfinite(float(v)) for v in values):
            continue
        volume_ratio = float(row["volume"]) / max(float(row["volume_mean"]), 1e-12)
        signal = (
            volume_ratio > VOLUME_MULTIPLIER
            and float(row["close"]) < float(row["sma40"])
            and float(row["fastd"]) > float(row["fastk"])
            and float(row["rsi"]) > RSI_FLOOR
            and float(row["fastd"]) > 1.0
            and float(row["fisher_rsi_norma"]) < FISHER_NORMA_CEILING
        )
        if not signal:
            continue
        ts = pd.Timestamp(row["minute"])
        if ts.date() < start:
            continue
        if last_signal is not None and ts - last_signal < pd.Timedelta(minutes=SYMBOL_DECLUSTER_MINUTES):
            continue
        last_signal = ts
        signal_close = float(row["close"])
        signal_low = float(row["low"])
        target = float(row["sma40"])
        for j in range(i + 1, min(i + 1 + CONFIRM_BARS, len(bars))):
            confirm = bars.iloc[j]
            if float(confirm["low"]) <= signal_low:
                break
            entry = float(confirm["close"])
            if not (entry > float(confirm["open"]) and entry > signal_close):
                continue
            planned_loss, net_r = _geometry(entry, signal_low, target)
            if math.isfinite(net_r) and net_r >= MIN_OBJECTIVE_NET_R:
                oversold_strength = max(0.0, (FISHER_NORMA_CEILING - float(row["fisher_rsi_norma"])) / FISHER_NORMA_CEILING)
                score = volume_ratio * (1.0 + oversold_strength) * net_r
                result.append(Candidate(
                    symbol=symbol, signal_ts=ts, entry_ts=pd.Timestamp(confirm["minute"]), entry=entry,
                    stop=signal_low, target=target, signal_close=signal_close, signal_low=signal_low,
                    sma40=target, rsi=float(row["rsi"]), fisher_rsi_norma=float(row["fisher_rsi_norma"]),
                    fastk=float(row["fastk"]), fastd=float(row["fastd"]), volume_ratio=volume_ratio,
                    planned_loss_rate=planned_loss, objective_net_r=net_r, score=score,
                ))
            break
    return result


def arbitrate(candidates: list[Candidate]) -> list[Candidate]:
    ordered = sorted(candidates, key=lambda c: (c.entry_ts, -c.score, c.symbol))
    out, bucket = [], []
    anchor = None
    for c in ordered:
        if anchor is None or c.entry_ts - anchor <= pd.Timedelta(minutes=GLOBAL_CLUSTER_MINUTES):
            if anchor is None: anchor = c.entry_ts
            bucket.append(c); continue
        out.append(max(bucket, key=lambda x: x.score)); bucket=[c]; anchor=c.entry_ts
    if bucket: out.append(max(bucket, key=lambda x: x.score))
    return out


def score(c: Candidate, minute: pd.DataFrame) -> Scored:
    start_i = int(minute.index.searchsorted(c.entry_ts, side="right"))
    end_i = min(start_i + MAX_HOLD_MINUTES, len(minute))
    exit_ts, exit_price, reason = c.entry_ts, c.entry, "TIME"
    mfe, mae = 0.0, 0.0
    for i in range(start_i, end_i):
        row=minute.iloc[i]; high=float(row["perp_high"]); low=float(row["perp_low"]); close=float(row["perp_close"])
        mfe=max(mfe, high/c.entry-1.0); mae=min(mae, low/c.entry-1.0)
        if low <= c.stop:
            exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), c.stop, "STOP"; break
        if high >= c.target:
            exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), c.target, "TARGET"; break
        exit_ts, exit_price = pd.Timestamp(row["minute"]), close
    net_return = exit_price/c.entry - 1.0 - COST_RATE
    return Scored(c, exit_ts, reason, exit_price, net_return, net_return/c.planned_loss_rate, mfe, mae)


def summarize(scored: list[Scored]) -> dict[str, object]:
    if not scored:
        return {"trades":0,"wins":0,"win_rate":0.0,"mean_net_r":0.0,"profit_factor_r":0.0,"target_rate":0.0,"stop_rate":0.0}
    r=np.array([x.net_r for x in scored]); gains=r[r>0].sum(); losses=-r[r<0].sum()
    return {"trades":len(scored),"wins":int((r>0).sum()),"win_rate":float((r>0).mean()),
            "mean_net_r":float(r.mean()),"median_net_r":float(np.median(r)),
            "profit_factor_r":float(gains/losses) if losses>0 else math.inf,
            "target_rate":sum(x.exit_reason=="TARGET" for x in scored)/len(scored),
            "stop_rate":sum(x.exit_reason=="STOP" for x in scored)/len(scored)}


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--start",required=True); p.add_argument("--end",required=True); p.add_argument("--cache",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    start=date.fromisoformat(a.start); end=date.fromisoformat(a.end); warm=start-timedelta(days=3)
    minutes={s:load_symbol(s,warm,end,a.cache) for s in SYMBOLS}; bars={s:aggregate_five(f) for s,f in minutes.items()}
    detected=[]
    for s in SYMBOLS: detected.extend(detect(s,bars[s],start))
    selected=arbitrate(detected); accepted=[]; occupied=None
    for c in selected:
        if occupied is not None and c.entry_ts <= occupied: continue
        x=score(c,minutes[c.symbol]); accepted.append(x); occupied=x.exit_ts
    a.output.mkdir(parents=True,exist_ok=True)
    def conv(v):
        if isinstance(v,pd.Timestamp): return v.isoformat()
        if isinstance(v,np.integer): return int(v)
        if isinstance(v,np.floating): return float(v)
        raise TypeError(type(v).__name__)
    payload=[{**asdict(x.candidate),**{k:v for k,v in asdict(x).items() if k!="candidate"}} for x in accepted]
    result={"study":"candidate-53-strategy005-capitulation-reclaim","start":a.start,"end":a.end,"cost_rate":COST_RATE,
            "detected":len(detected),"global_arbitrated":len(selected),"single_slot_scored":len(accepted),"summary":summarize(accepted)}
    (a.output/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n")
    (a.output/"trades.json").write_text(json.dumps(payload,indent=2,sort_keys=True,default=conv,allow_nan=False)+"\n")
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))

if __name__=="__main__": main()
