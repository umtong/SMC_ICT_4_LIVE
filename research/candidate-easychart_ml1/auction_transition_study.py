#!/usr/bin/env python3
"""Causal cross-market auction-transition candidate harvest.

This research module does not treat the existing EasyChart policy as a benchmark.
It builds a new, symbol-agnostic opportunity set from three reusable market
mechanisms: first-touch liquidity sweep/reclaim, accepted break/first pullback,
and strictly-later cross-market residual convergence.  Plans freeze entry, stop
and target before entry.  Labels are added only after generation.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from data_re1_flow import load_range_flow

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TICKS = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.01, "XRPUSDT": 0.0001}
TAKER = 0.00050
MAKER = 0.00020
ENTRY_SLIPPAGE_TICKS = 2
STOP_SLIPPAGE_TICKS = 2
MAX_HOLD_MINUTES = 720
NS_MINUTE = 60_000_000_000


def _prior_median(s: pd.Series, window: int = 1440, floor: float = 1e-12) -> pd.Series:
    prior = s.shift(1)
    return prior.rolling(window, min_periods=60).median().combine_first(
        prior.expanding(min_periods=1).median(),
    ).fillna(floor).clip(lower=floor)


def _rolling_sum(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).sum()


def _path_eff(ret: pd.Series, n: int) -> pd.Series:
    net = ret.rolling(n, min_periods=n).sum()
    total = ret.abs().rolling(n, min_periods=n).sum()
    return net / total.replace(0.0, np.nan)


def make_features(symbol: str, raw: pd.DataFrame) -> pd.DataFrame:
    d = raw.copy().sort_values("open_time_dt")
    d["ts"] = pd.DatetimeIndex(d["open_time_dt"]) + pd.Timedelta(minutes=1)
    d = d.set_index("ts", drop=True)
    close = d["close"].astype(float).clip(lower=1e-12)
    log_close = np.log(close)
    ret1 = log_close.diff()
    sigma = _prior_median(ret1.abs(), 1440, 1e-8)
    range_frac = (d["high"] - d["low"]) / close
    range_base = _prior_median(range_frac, 1440, 1e-8)
    quote = d["quote_volume"].astype(float).clip(lower=0.0)
    signed = 2.0 * d["taker_buy_quote_volume"].astype(float) - quote
    count = d["count"].astype(float).clip(lower=0.0)
    out = d[["open", "high", "low", "close", "volume", "quote_volume", "count", "taker_buy_quote_volume"]].copy()
    out["symbol"] = symbol
    out["ret1"] = ret1
    out["prior_sigma"] = sigma
    out["prior_range"] = range_base
    out["range_ratio"] = range_frac / range_base
    out["activity_ratio"] = quote / _prior_median(quote, 1440, 1.0)
    out["trade_count_ratio"] = count / _prior_median(count, 1440, 1.0)
    out["delta_share_1"] = signed / quote.replace(0.0, np.nan)
    prange = (d["high"] - d["low"]).replace(0.0, np.nan)
    out["body_fraction"] = (d["close"] - d["open"]) / prange
    out["close_location"] = (d["close"] - d["low"]) / prange
    for n in (5, 15, 30, 60):
        r = log_close.diff(n)
        q = _rolling_sum(quote, n)
        sd = _rolling_sum(signed, n)
        out[f"ret_z_{n}"] = r / (sigma * math.sqrt(n))
        out[f"path_eff_{n}"] = _path_eff(ret1, n)
        out[f"delta_share_{n}"] = sd / q.replace(0.0, np.nan)
        out[f"flow_progress_{n}"] = out[f"ret_z_{n}"] * out[f"delta_share_{n}"]
    return out.replace([np.inf, -np.inf], np.nan)


def add_cross_features(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    for n in (5, 15, 30, 60):
        panel = pd.concat({s: f[f"ret_z_{n}"] for s, f in frames.items()}, axis=1)
        common = panel.median(axis=1)
        breadth_pos = panel.gt(0.0).mean(axis=1)
        breadth_neg = panel.lt(0.0).mean(axis=1)
        dispersion = panel.sub(common, axis=0).abs().median(axis=1)
        for symbol, frame in frames.items():
            frame[f"common_z_{n}"] = common.reindex(frame.index)
            frame[f"residual_z_{n}"] = frame[f"ret_z_{n}"] - frame[f"common_z_{n}"]
            frame[f"breadth_pos_{n}"] = breadth_pos.reindex(frame.index)
            frame[f"breadth_neg_{n}"] = breadth_neg.reindex(frame.index)
            frame[f"dispersion_{n}"] = dispersion.reindex(frame.index)
    return frames


def aggregate(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    cols = ["open", "high", "low", "close", "quote_volume", "count", "taker_buy_quote_volume"]
    x = frame[cols].copy()
    return x.resample(f"{minutes}min", label="right", closed="right", origin="epoch").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
        quote_volume=("quote_volume", "sum"), count=("count", "sum"),
        taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
    ).dropna()


@dataclass(frozen=True)
class Pivot:
    pivot_id: str
    timeframe: int
    span: int
    side: str
    price: float
    event_ts: pd.Timestamp
    observed_ts: pd.Timestamp
    strength: float


def confirmed_pivots(frame: pd.DataFrame, timeframe: int, spans: Iterable[int]) -> list[Pivot]:
    bars = aggregate(frame, timeframe)
    result: list[Pivot] = []
    highs = bars["high"].to_numpy(float)
    lows = bars["low"].to_numpy(float)
    times = bars.index
    ranges = (bars["high"] - bars["low"]).to_numpy(float)
    for span in spans:
        for i in range(span, len(bars) - span):
            hwin = highs[i-span:i+span+1]
            lwin = lows[i-span:i+span+1]
            observed_i = i + span
            local = max(float(np.median(np.r_[ranges[max(0,i-span):i], ranges[i+1:i+span+1]])), 1e-12)
            if highs[i] == hwin.max() and int(np.sum(hwin == highs[i])) == 1:
                prominence = min(highs[i] - lwin[:span].min(), highs[i] - lwin[span+1:].min())
                result.append(Pivot(f"{timeframe}m-H-{i}-s{span}", timeframe, span, "HIGH", float(highs[i]), times[i], times[observed_i], float(prominence/local)))
            if lows[i] == lwin.min() and int(np.sum(lwin == lows[i])) == 1:
                prominence = min(hwin[:span].max() - lows[i], hwin[span+1:].max() - lows[i])
                result.append(Pivot(f"{timeframe}m-L-{i}-s{span}", timeframe, span, "LOW", float(lows[i]), times[i], times[observed_i], float(prominence/local)))
    return sorted(result, key=lambda p: (p.observed_ts, p.timeframe, p.span, p.pivot_id))


def nearest_opposing_target(pivots: list[Pivot], side: int, entry: float, asof: pd.Timestamp) -> tuple[Pivot, float] | None:
    if side > 0:
        candidates = [p for p in pivots if p.side == "HIGH" and p.observed_ts < asof and p.price > entry]
        return None if not candidates else (min(candidates, key=lambda p: (p.price, -p.timeframe, -p.span, p.pivot_id)), min(p.price for p in candidates))
    candidates = [p for p in pivots if p.side == "LOW" and p.observed_ts < asof and p.price < entry]
    return None if not candidates else (max(candidates, key=lambda p: (p.price, p.timeframe, p.span, p.pivot_id)), max(p.price for p in candidates))


def snapshot_features(frame: pd.DataFrame, ts: pd.Timestamp, side: int) -> dict[str, float]:
    if ts not in frame.index:
        loc = frame.index.searchsorted(ts, side="right") - 1
        if loc < 0:
            return {}
        row = frame.iloc[loc]
    else:
        row = frame.loc[ts]
    out: dict[str, float] = {}
    aligned = {"ret_z", "path_eff", "delta_share", "flow_progress", "common_z", "residual_z", "body_fraction"}
    for key, value in row.items():
        if not isinstance(key, str):
            continue
        if key in {"symbol"} or key in {"open", "high", "low", "close", "volume", "quote_volume", "count", "taker_buy_quote_volume"}:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        if any(key.startswith(token) for token in aligned):
            number *= side
        if key.startswith("breadth_pos_"):
            n = key.rsplit("_", 1)[-1]
            number = float(row[f"breadth_pos_{n}"] if side > 0 else row[f"breadth_neg_{n}"])
            out[f"aligned_breadth_{n}"] = number
            continue
        if key.startswith("breadth_neg_"):
            continue
        out[key] = number
    return out


def economics(side: int, entry: float, stop: float, target: float, tick: float) -> dict[str, float]:
    risk = abs(entry - stop)
    sign = float(side)
    entry_fill = entry + sign * ENTRY_SLIPPAGE_TICKS * tick
    stop_fill = stop - sign * STOP_SLIPPAGE_TICKS * tick
    target_fill = target
    win_gross = sign * (target_fill - entry_fill) / risk
    loss_gross = sign * (stop_fill - entry_fill) / risk
    win_net = win_gross - abs(entry_fill) * TAKER / risk - abs(target_fill) * MAKER / risk
    loss_net = loss_gross - abs(entry_fill) * TAKER / risk - abs(stop_fill) * TAKER / risk
    fixed_win = win_net / abs(loss_net) if loss_net < 0.0 else -math.inf
    break_even = 1.0 / (1.0 + fixed_win) if fixed_win > 0 else 1.0
    return {"gross_rr": abs(target-entry)/risk, "win_net_price_r": win_net, "loss_net_price_r": loss_net, "fixed_risk_win_r": fixed_win, "break_even_probability": break_even}


def label_plan(frame: pd.DataFrame, side: int, entry_ts: pd.Timestamp, entry: float, stop: float, target: float, tick: float) -> dict[str, Any]:
    econ = economics(side, entry, stop, target, tick)
    future = frame.loc[frame.index >= entry_ts].head(MAX_HOLD_MINUTES)
    outcome = "UNRESOLVED"; resolution = pd.NaT
    for ts, bar in future.iterrows():
        stop_hit = float(bar.low) <= stop if side > 0 else float(bar.high) >= stop
        target_hit = float(bar.high) >= target if side > 0 else float(bar.low) <= target
        if stop_hit:
            outcome = "STOP_FIRST" if not target_hit else "AMBIGUOUS_SAME_MINUTE"
            resolution = ts; break
        if target_hit:
            outcome = "TARGET_FIRST"; resolution = ts; break
    label = 1.0 if outcome == "TARGET_FIRST" else 0.0 if outcome in {"STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"} else np.nan
    net_r = econ["fixed_risk_win_r"] if label == 1.0 else -1.0 if label == 0.0 else np.nan
    return {**econ, "outcome": outcome, "label": label, "net_r": net_r, "resolution_ts": None if pd.isna(resolution) else resolution.isoformat(), "minutes_to_resolution": None if pd.isna(resolution) else int((resolution-entry_ts)/pd.Timedelta(minutes=1))}


def add_plan(plans: list[dict[str, Any]], *, symbol: str, family: str, side: int, decision_ts: pd.Timestamp, entry_ts: pd.Timestamp, entry: float, stop: float, target: float, causal_id: str, frame: pd.DataFrame, specifics: dict[str, Any]) -> None:
    tick = TICKS[symbol]
    if not (entry > 0 and abs(entry-stop) > 0 and abs(target-entry) > 0): return
    if side > 0 and not (stop < entry < target): return
    if side < 0 and not (target < entry < stop): return
    econ = economics(side, entry, stop, target, tick)
    if econ["gross_rr"] + 1e-12 < 1.0 or econ["fixed_risk_win_r"] <= 0.0: return
    row: dict[str, Any] = {"plan_id": f"AT:{family}:{symbol}:{causal_id}", "causal_event_id": causal_id, "symbol": symbol, "family": family, "side": "LONG" if side>0 else "SHORT", "side_sign": side, "decision_ts": decision_ts.isoformat(), "entry_ts": entry_ts.isoformat(), "entry": entry, "stop": stop, "target": target, **specifics}
    row.update(snapshot_features(frame, decision_ts, side))
    row.update(label_plan(frame, side, entry_ts, entry, stop, target, tick))
    plans.append(row)


def harvest_sweep_reclaim(symbol: str, frame: pd.DataFrame, pivots: list[Pivot], start_ts: pd.Timestamp, plans: list[dict[str, Any]]) -> None:
    """Harvest only the first later interaction, using vectorized first-hit lookup."""
    tick = TICKS[symbol]
    idx = frame.index
    lows = frame["low"].to_numpy(float)
    highs = frame["high"].to_numpy(float)
    closes = frame["close"].to_numpy(float)
    opens = frame["open"].to_numpy(float)
    for pivot in pivots:
        start_i = idx.searchsorted(max(pivot.observed_ts + pd.Timedelta(minutes=1), start_ts), side="left")
        if start_i >= len(frame) - 2:
            continue
        touched = lows[start_i:-2] <= pivot.price if pivot.side == "LOW" else highs[start_i:-2] >= pivot.price
        hits = np.flatnonzero(touched)
        if not len(hits):
            continue
        i = start_i + int(hits[0])
        if pivot.side == "LOW":
            reclaimed = closes[i - 1] >= pivot.price and closes[i] > pivot.price
            side = 1
        else:
            reclaimed = closes[i - 1] <= pivot.price and closes[i] < pivot.price
            side = -1
        if not reclaimed:
            continue
        bar = frame.iloc[i]
        sweep_ts = idx[i]
        target_info = nearest_opposing_target(pivots, side, closes[i], sweep_ts)
        if target_info is None:
            continue
        target_pivot, target = target_info
        confirmed_i = None
        for j in range(i + 1, min(i + 7, len(frame) - 1)):
            if side > 0:
                invalid = lows[j] <= lows[i] - tick
                confirm = closes[j] > max(pivot.price, closes[i]) and closes[j] > opens[j]
            else:
                invalid = highs[j] >= highs[i] + tick
                confirm = closes[j] < min(pivot.price, closes[i]) and closes[j] < opens[j]
            if invalid:
                break
            if confirm:
                confirmed_i = j
                break
        if confirmed_i is None:
            continue
        decision_ts = idx[confirmed_i]
        entry_ts = idx[confirmed_i + 1]
        entry = opens[confirmed_i + 1]
        stop = lows[i] - tick if side > 0 else highs[i] + tick
        sigma = max(float(frame.iloc[i].prior_sigma), 1e-12)
        specifics = {
            "level_timeframe": pivot.timeframe,
            "level_span": pivot.span,
            "level_strength": pivot.strength,
            "level_age_minutes": (sweep_ts - pivot.observed_ts) / pd.Timedelta(minutes=1),
            "sweep_depth_sigma": ((pivot.price - lows[i]) / closes[i] / sigma if side > 0 else (highs[i] - pivot.price) / closes[i] / sigma),
            "response_delay_minutes": confirmed_i - i,
            "target_timeframe": target_pivot.timeframe,
            "target_span": target_pivot.span,
            "target_strength": target_pivot.strength,
        }
        add_plan(plans, symbol=symbol, family="FIRST_TOUCH_SWEEP_RECLAIM", side=side, decision_ts=decision_ts, entry_ts=entry_ts, entry=entry, stop=stop, target=target, causal_id=f"{pivot.pivot_id}:{int(sweep_ts.value)}", frame=frame, specifics=specifics)


def harvest_break_pullback(symbol: str, frame: pd.DataFrame, pivots: list[Pivot], start_ts: pd.Timestamp, plans: list[dict[str, Any]]) -> None:
    """Find the first accepted break without scanning every future bar per pivot."""
    tick = TICKS[symbol]
    idx = frame.index
    bars5 = aggregate(frame, 5)
    bopen = bars5["open"].to_numpy(float)
    bclose = bars5["close"].to_numpy(float)
    lows = frame["low"].to_numpy(float)
    highs = frame["high"].to_numpy(float)
    closes = frame["close"].to_numpy(float)
    opens = frame["open"].to_numpy(float)
    for pivot in pivots:
        if pivot.timeframe not in {5, 15}:
            continue
        bstart = max(1, bars5.index.searchsorted(max(pivot.observed_ts + pd.Timedelta(minutes=5), start_ts), side="left"))
        if bstart >= len(bars5) - 2:
            continue
        if pivot.side == "HIGH":
            condition = (bclose[bstart - 1:-3] <= pivot.price) & (bclose[bstart:-2] > pivot.price) & (bclose[bstart:-2] > bopen[bstart:-2])
            side = 1
        else:
            condition = (bclose[bstart - 1:-3] >= pivot.price) & (bclose[bstart:-2] < pivot.price) & (bclose[bstart:-2] < bopen[bstart:-2])
            side = -1
        hits = np.flatnonzero(condition)
        if not len(hits):
            continue
        k = bstart + int(hits[0])
        break_ts = bars5.index[k]
        hold = bars5.iloc[k + 1]
        held = (float(hold.open) > pivot.price and float(hold.close) > pivot.price) if side > 0 else (float(hold.open) < pivot.price and float(hold.close) < pivot.price)
        if not held:
            continue
        target_info = nearest_opposing_target(pivots, side, float(hold.close), break_ts)
        if target_info is None:
            continue
        target_pivot, target = target_info
        mstart = idx.searchsorted(bars5.index[k + 1] + pd.Timedelta(minutes=1), side="left")
        retest_i = None
        for i in range(mstart, min(mstart + 121, len(frame) - 2)):
            if (side > 0 and lows[i] <= pivot.price and closes[i] > pivot.price) or (side < 0 and highs[i] >= pivot.price and closes[i] < pivot.price):
                retest_i = i
                break
            if (side > 0 and closes[i] < pivot.price) or (side < 0 and closes[i] > pivot.price):
                break
        if retest_i is None:
            continue
        confirm_i = None
        for j in range(retest_i + 1, min(retest_i + 7, len(frame) - 1)):
            confirm = (closes[j] > highs[retest_i] and closes[j] > opens[j]) if side > 0 else (closes[j] < lows[retest_i] and closes[j] < opens[j])
            fail = closes[j] < pivot.price if side > 0 else closes[j] > pivot.price
            if fail:
                break
            if confirm:
                confirm_i = j
                break
        if confirm_i is None:
            continue
        decision_ts = idx[confirm_i]
        entry_ts = idx[confirm_i + 1]
        entry = opens[confirm_i + 1]
        stop = min(lows[retest_i], lows[confirm_i], pivot.price - tick) - tick if side > 0 else max(highs[retest_i], highs[confirm_i], pivot.price + tick) + tick
        sigma = max(float(frame.iloc[confirm_i].prior_sigma), 1e-12)
        specifics = {
            "level_timeframe": pivot.timeframe,
            "level_span": pivot.span,
            "level_strength": pivot.strength,
            "level_age_minutes": (break_ts - pivot.observed_ts) / pd.Timedelta(minutes=1),
            "hold_distance_sigma": abs(float(hold.close) - pivot.price) / float(hold.close) / sigma,
            "pullback_delay_minutes": retest_i - mstart,
            "response_delay_minutes": confirm_i - retest_i,
            "target_timeframe": target_pivot.timeframe,
            "target_span": target_pivot.span,
            "target_strength": target_pivot.strength,
        }
        add_plan(plans, symbol=symbol, family="ACCEPTED_BREAK_FIRST_PULLBACK", side=side, decision_ts=decision_ts, entry_ts=entry_ts, entry=entry, stop=stop, target=target, causal_id=f"{pivot.pivot_id}:{int(break_ts.value)}", frame=frame, specifics=specifics)


def harvest_residual(symbol: str, frame: pd.DataFrame, start_ts: pd.Timestamp, plans: list[dict[str, Any]]) -> None:
    idx=frame.index; tick=TICKS[symbol]; rz=frame["residual_z_15"]
    qlo=rz.shift(1).rolling(1440,min_periods=240).quantile(0.05); qhi=rz.shift(1).rolling(1440,min_periods=240).quantile(0.95)
    i=max(idx.searchsorted(start_ts),240); cooldown=-1
    while i<len(frame)-12:
        if i<=cooldown: i+=1; continue
        z=float(rz.iloc[i]) if pd.notna(rz.iloc[i]) else 0.0
        side=1 if pd.notna(qlo.iloc[i]) and z<min(float(qlo.iloc[i]),-1.5) else -1 if pd.notna(qhi.iloc[i]) and z>max(float(qhi.iloc[i]),1.5) else 0
        if side==0: i+=1; continue
        event=frame.iloc[i]; confirm_i=None
        for j in range(i+1,min(i+11,len(frame)-1)):
            r=frame.iloc[j]; zj=float(rz.iloc[j]) if pd.notna(rz.iloc[j]) else z
            contraction=(zj-z)>=0.5 if side>0 else (z-zj)>=0.5
            reversal=(float(r.close)>float(frame.iloc[j-1].high) and float(r.close)>float(r.open)) if side>0 else (float(r.close)<float(frame.iloc[j-1].low) and float(r.close)<float(r.open))
            if contraction and reversal: confirm_i=j; break
        if confirm_i is None: cooldown=i+10; i+=1; continue
        decision_ts=idx[confirm_i]; entry_ts=idx[confirm_i+1]; entry=float(frame.iloc[confirm_i+1].open)
        excursion=frame.iloc[i:confirm_i+1]
        stop=float(excursion.low.min())-tick if side>0 else float(excursion.high.max())+tick
        sigma=max(float(event.prior_sigma),1e-12)
        # Half convergence toward the common 15m move; a real first obstacle, not an arbitrary RR target.
        start_price=float(frame.iloc[max(0,i-15)].close); common=float(event.common_z_15)
        fair=start_price*math.exp(common*sigma*math.sqrt(15))
        target=entry+0.5*(fair-entry)
        if side>0 and target<=entry: target=float(event.close)
        if side<0 and target>=entry: target=float(event.close)
        specifics={"residual_start":z,"residual_at_confirmation":float(rz.iloc[confirm_i]),"residual_contraction":abs(z)-abs(float(rz.iloc[confirm_i])),"response_delay_minutes":confirm_i-i,"event_activity_ratio":float(event.activity_ratio),"event_delta_share_15_aligned":side*float(event.delta_share_15),"event_path_eff_15_aligned":side*float(event.path_eff_15)}
        add_plan(plans,symbol=symbol,family="CROSS_RESIDUAL_CONVERGENCE",side=side,decision_ts=decision_ts,entry_ts=entry_ts,entry=entry,stop=stop,target=target,causal_id=f"R15:{int(idx[i].value)}",frame=frame,specifics=specifics)
        cooldown=confirm_i+15; i=confirm_i+1


def parse_args() -> argparse.Namespace:
    p=argparse.ArgumentParser(); p.add_argument("--start",type=date.fromisoformat,required=True); p.add_argument("--end",type=date.fromisoformat,required=True); p.add_argument("--warmup-days",type=int,default=10); p.add_argument("--cache",type=Path,required=True); p.add_argument("--output",type=Path,required=True); return p.parse_args()


def main() -> None:
    a=parse_args(); a.output.mkdir(parents=True,exist_ok=True); load_start=a.start-timedelta(days=a.warmup_days)
    raw={s:load_range_flow(s,load_start,a.end,a.cache) for s in SYMBOLS}
    features=add_cross_features({s:make_features(s,f) for s,f in raw.items()})
    start_ts=pd.Timestamp(a.start,tz="UTC"); plans:list[dict[str,Any]]=[]; pivot_summary={}
    for symbol,frame in features.items():
        pivots=confirmed_pivots(frame,5,(2,4))+confirmed_pivots(frame,15,(2,4))
        pivots=sorted(pivots,key=lambda p:(p.observed_ts,p.timeframe,p.span,p.pivot_id)); pivot_summary[symbol]=len(pivots)
        harvest_sweep_reclaim(symbol,frame,pivots,start_ts,plans)
        harvest_break_pullback(symbol,frame,pivots,start_ts,plans)
        harvest_residual(symbol,frame,start_ts,plans)
    out=pd.DataFrame(plans)
    if not out.empty:
        out=out.drop_duplicates("plan_id").sort_values(["entry_ts","symbol","family","plan_id"],kind="mergesort")
    out.to_csv(a.output/"candidates.csv",index=False)
    summary={"start":a.start.isoformat(),"end":a.end.isoformat(),"rows":int(len(out)),"resolved":int(out["label"].notna().sum()) if not out.empty else 0,"target_first_rate":float(out["label"].mean()) if not out.empty else None,"mean_net_r":float(out["net_r"].mean()) if not out.empty else None,"plans_per_day":float(len(out)/((a.end-a.start).days+1)),"pivot_counts":pivot_summary,"by_family":({k:{"rows":int(len(g)),"resolved":int(g["label"].notna().sum()),"target_first_rate":float(g["label"].mean()),"mean_net_r":float(g["net_r"].mean())} for k,g in out.groupby("family")} if not out.empty else {})}
    (a.output/"summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps(summary,indent=2,sort_keys=True))

if __name__=="__main__": main()
