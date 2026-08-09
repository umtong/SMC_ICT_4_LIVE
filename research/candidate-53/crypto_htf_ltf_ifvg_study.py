#!/usr/bin/env python3
"""Crypto adaptation of an external HTF-FVG -> LTF-IFVG trading system.

Source mined from prashanthaitha24/nq-strategy-b-bot.  The external NQ system's
causal decision flow is preserved while fixed NQ point distances are converted
to volatility-relative geometry for transfer across BTC/ETH/SOL/XRP:

    active 15m FVG context
      -> active same-direction 5m FVG nested inside it
      -> completed 5m bar retests the 5m FVG but closes back beyond its edge
      -> entry strictly at next 5m bar open
      -> invalidation beyond 5m FVG + small ATR buffer
      -> target = 2R
      -> time exit after 6h if unresolved.

The external backtest used min gap=3 NQ points, stop buffer=2 points, HTF overlap
buffer=5 points, 5m FVG max-age=2h and 15m FVG max-age=24h.  Point distances
cannot be copied across instruments, so ONLY those three distances are
normalized: min gap=0.10 completed 5m ATR, stop buffer=0.05 ATR, containment
buffer=0.20 completed 15m ATR.  These are single transfer defaults, not a grid.
The 2h/24h ages, next-bar entry and 2R target are copied directly.

Unlike the external research helper, mitigation is permanent once a completed
bar closes through an FVG midpoint.  Each 5m FVG may generate at most one trade,
so trade count cannot be inflated by repeated retests of the same causal zone.
Long and short are both tested because the NQ long-only asymmetry is not assumed
to transfer to crypto.

This is a causal trade-geometry screen only.  No custom account, matching,
leverage or position-sizing engine is created.  Exact 1m paths use conservative
stop-before-target ordering and the current 21 bp round-trip hurdle.  If the
family survives, it must be promoted to NautilusTrader for account/NAV proof.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive, StudyError, download_verified, read_kline

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COST_RATE = 0.0021
MIN_GAP_ATR = 0.10
STOP_BUFFER_ATR = 0.05
HTF_OVERLAP_ATR = 0.20
FVG5_MAX_AGE = pd.Timedelta(hours=2)
FVG15_MAX_AGE = pd.Timedelta(hours=24)
MAX_HOLD_MINUTES = 360
TARGET_R = 2.0
ATR_PERIOD_5M = 20
ATR_PERIOD_15M = 20
GLOBAL_CLUSTER_MINUTES = 3


@dataclass(slots=True)
class FVG:
    id: str
    side: int
    bottom: float
    top: float
    midpoint: float
    formed_ts: pd.Timestamp
    atr_at_formation: float
    timeframe: str
    mitigated: bool = False
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class Setup:
    symbol: str
    side: int
    signal_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    entry: float
    stop: float
    target: float
    fvg5_id: str
    fvg15_id: str
    fvg5_gap_atr: float
    fvg15_gap_atr: float
    fvg5_age_minutes: float
    fvg15_age_minutes: float
    nesting_slack_atr: float
    risk_rate: float
    target_net_r: float
    score: float


@dataclass(frozen=True, slots=True)
class Scored:
    setup: Setup
    exit_ts: pd.Timestamp
    exit_price: float
    exit_reason: str
    gross_return: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def month_labels(year: int) -> list[str]:
    return [f"{year}-{month:02d}" for month in range(1, 13)]


def load_symbol(symbol: str, year: int, cache: Path) -> pd.DataFrame:
    labels = [f"{year - 1}-12", *month_labels(year), f"{year + 1}-01"]
    keyed: dict[str, pd.DataFrame] = {}
    def fetch(label: str):
        path = download_verified(
            Archive("um", "monthly", "klines", symbol, label, "1m"),
            cache / symbol,
        )
        return label, read_kline(path, prefix="perp")
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(fetch, label) for label in labels]
        for job in as_completed(jobs):
            label, frame = job.result()
            keyed[label] = frame
    panel = pd.concat([keyed[label] for label in labels], ignore_index=True)
    panel["minute"] = pd.to_datetime(panel["minute"], utc=True, errors="raise")
    panel = panel.sort_values("minute", kind="stable").drop_duplicates("minute", keep="last")
    if panel["minute"].duplicated().any() or not panel["minute"].is_monotonic_increasing:
        raise StudyError(f"invalid minute clock: {symbol}")
    return panel.set_index("minute", drop=False)


def aggregate(panel: pd.DataFrame, minutes: int) -> pd.DataFrame:
    grouped = panel.resample(f"{minutes}min", label="left", closed="left")
    result = pd.DataFrame({
        "open": grouped["perp_open"].first(),
        "high": grouped["perp_high"].max(),
        "low": grouped["perp_low"].min(),
        "close": grouped["perp_close"].last(),
        "volume": grouped["perp_quote_volume"].sum(),
        "count": grouped["perp_close"].count(),
    })
    result = result[result["count"].eq(minutes)].copy()
    # Timestamp at the last minute which completed the bar, not interval start.
    result.index = result.index + pd.Timedelta(minutes=minutes - 1)
    result["completed_ts"] = result.index
    prev = result["close"].shift(1)
    tr = pd.concat([
        result["high"] - result["low"],
        (result["high"] - prev).abs(),
        (result["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    period = ATR_PERIOD_5M if minutes == 5 else ATR_PERIOD_15M
    result["atr"] = tr.rolling(period, min_periods=period).mean().shift(1)
    return result


def detect_fvgs(frame: pd.DataFrame, timeframe: str) -> list[FVG]:
    records: list[FVG] = []
    rows = list(frame.itertuples())
    for i in range(2, len(rows)):
        b0, b2 = rows[i - 2], rows[i]
        atr = float(b2.atr)
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        formed = pd.Timestamp(b2.Index)
        bull_gap = float(b2.low) - float(b0.high)
        if bull_gap >= MIN_GAP_ATR * atr:
            bottom, top = float(b0.high), float(b2.low)
            records.append(FVG(
                id=f"{timeframe}:B:{formed.isoformat()}:{bottom:.12g}:{top:.12g}",
                side=1, bottom=bottom, top=top, midpoint=(bottom + top) / 2.0,
                formed_ts=formed, atr_at_formation=atr, timeframe=timeframe,
            ))
        bear_gap = float(b0.low) - float(b2.high)
        if bear_gap >= MIN_GAP_ATR * atr:
            bottom, top = float(b2.high), float(b0.low)
            records.append(FVG(
                id=f"{timeframe}:S:{formed.isoformat()}:{bottom:.12g}:{top:.12g}",
                side=-1, bottom=bottom, top=top, midpoint=(bottom + top) / 2.0,
                formed_ts=formed, atr_at_formation=atr, timeframe=timeframe,
            ))
    return records


def active_and_update(
    fvgs: list[FVG],
    ts: pd.Timestamp,
    close: float,
    max_age: pd.Timedelta,
) -> list[FVG]:
    active: list[FVG] = []
    for fvg in fvgs:
        if fvg.formed_ts >= ts or fvg.mitigated:
            continue
        if ts - fvg.formed_ts > max_age:
            continue
        # Permanent completed-bar mitigation through midpoint.
        if (fvg.side > 0 and close < fvg.midpoint) or (fvg.side < 0 and close > fvg.midpoint):
            fvg.mitigated = True
            continue
        active.append(fvg)
    return active


def detect_setups(symbol: str, panel: pd.DataFrame, year: int) -> list[Setup]:
    five = aggregate(panel, 5)
    fifteen = aggregate(panel, 15)
    fvgs5 = detect_fvgs(five, "5m")
    fvgs15 = detect_fvgs(fifteen, "15m")
    # Sort once; active checks are intentionally transparent and low-cost at annual scale.
    core_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    core_end = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
    setups: list[Setup] = []

    five_rows = list(five.itertuples())
    for i, bar in enumerate(five_rows[:-1]):
        ts = pd.Timestamp(bar.Index)
        if ts < core_start or ts >= core_end:
            continue
        close, low, high = float(bar.close), float(bar.low), float(bar.high)
        atr5 = float(bar.atr)
        if not math.isfinite(atr5) or atr5 <= 0.0:
            continue
        active5 = active_and_update(fvgs5, ts, close, FVG5_MAX_AGE)
        active15 = active_and_update(fvgs15, ts, close, FVG15_MAX_AGE)
        if not active5 or not active15:
            continue

        chosen: tuple[FVG, FVG, float] | None = None
        for f5 in active5:
            if f5.consumed:
                continue
            retest = (
                f5.bottom <= low <= f5.top and close > f5.top
                if f5.side > 0
                else f5.bottom <= high <= f5.top and close < f5.bottom
            )
            if not retest:
                continue
            same15 = [f15 for f15 in active15 if f15.side == f5.side]
            for f15 in same15:
                buffer = HTF_OVERLAP_ATR * f15.atr_at_formation
                nested = (
                    f5.bottom >= f15.bottom - 1e-12
                    and f5.top <= f15.top + buffer + 1e-12
                )
                if not nested:
                    continue
                # Prefer the tightest HTF containment, not hindsight outcome.
                slack = max(0.0, f15.top + buffer - f5.top) + max(0.0, f5.bottom - f15.bottom)
                if chosen is None or slack < chosen[2]:
                    chosen = (f5, f15, slack)
        if chosen is None:
            continue
        f5, f15, slack = chosen
        next_bar = five_rows[i + 1]
        entry_ts = pd.Timestamp(next_bar.Index) - pd.Timedelta(minutes=4)
        # Next 5m bar open is known only as that bar begins.
        entry = float(next_bar.open)
        if not math.isfinite(entry) or entry <= 0.0:
            continue
        stop_buffer = STOP_BUFFER_ATR * atr5
        stop = f5.bottom - stop_buffer if f5.side > 0 else f5.top + stop_buffer
        risk_abs = f5.side * (entry - stop)
        if not (math.isfinite(stop) and stop > 0.0 and risk_abs > 0.0):
            continue
        target = entry + f5.side * TARGET_R * risk_abs
        risk_rate = risk_abs / entry + COST_RATE
        reward_rate = TARGET_R * risk_abs / entry - COST_RATE
        target_net_r = reward_rate / risk_rate
        if target_net_r <= 0.0:
            continue
        f5.consumed = True
        score = (
            (f5.top - f5.bottom) / max(f5.atr_at_formation, 1e-12)
            + (f15.top - f15.bottom) / max(f15.atr_at_formation, 1e-12)
            - slack / max(f15.atr_at_formation, 1e-12)
        )
        setups.append(Setup(
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


def score_setup(setup: Setup, panel: pd.DataFrame) -> Scored:
    start = setup.entry_ts
    path = panel[(panel.index >= start) & (panel.index < start + pd.Timedelta(minutes=MAX_HOLD_MINUTES))]
    exit_ts, exit_price, reason = setup.entry_ts, setup.entry, "TIME"
    mfe, mae = 0.0, 0.0
    for row in path.itertuples():
        high, low, close = float(row.perp_high), float(row.perp_low), float(row.perp_close)
        if setup.side > 0:
            mfe = max(mfe, high / setup.entry - 1.0)
            mae = min(mae, low / setup.entry - 1.0)
            if low <= setup.stop:
                exit_ts, exit_price, reason = pd.Timestamp(row.minute), setup.stop, "STOP"; break
            if high >= setup.target:
                exit_ts, exit_price, reason = pd.Timestamp(row.minute), setup.target, "TARGET"; break
        else:
            mfe = max(mfe, setup.entry / low - 1.0)
            mae = min(mae, setup.entry / high - 1.0)
            if high >= setup.stop:
                exit_ts, exit_price, reason = pd.Timestamp(row.minute), setup.stop, "STOP"; break
            if low <= setup.target:
                exit_ts, exit_price, reason = pd.Timestamp(row.minute), setup.target, "TARGET"; break
        exit_ts, exit_price = pd.Timestamp(row.minute), close
    gross = setup.side * (exit_price / setup.entry - 1.0)
    net = gross - COST_RATE
    return Scored(setup, exit_ts, exit_price, reason, gross, net, net / setup.risk_rate, mfe, mae)


def global_single_position(items: list[Scored]) -> list[Scored]:
    ordered = sorted(items, key=lambda x: (x.setup.entry_ts, -x.setup.score, x.setup.symbol))
    result: list[Scored] = []
    free_at: pd.Timestamp | None = None
    i = 0
    while i < len(ordered):
        item = ordered[i]
        if free_at is not None and item.setup.entry_ts <= free_at:
            i += 1; continue
        cluster = [item]; j = i + 1
        while j < len(ordered) and ordered[j].setup.entry_ts - item.setup.entry_ts <= pd.Timedelta(minutes=GLOBAL_CLUSTER_MINUTES):
            cluster.append(ordered[j]); j += 1
        chosen = max(cluster, key=lambda x: (x.setup.score, x.setup.symbol))
        result.append(chosen); free_at = chosen.exit_ts; i = j
    return result


def stats(items: list[Scored], calendar_days: int) -> dict[str, object]:
    if not items:
        return {"trades":0,"wins":0,"win_rate":0.0,"mean_net_r":0.0,"median_net_r":0.0,"profit_factor_r":0.0,
                "mean_net_return":0.0,"target_rate":0.0,"stop_rate":0.0,"time_rate":0.0,"trades_per_calendar_day":0.0}
    rs = np.asarray([x.net_r for x in items], dtype=float)
    gains = rs[rs > 0].sum(); losses = -rs[rs < 0].sum()
    return {
        "trades":len(items),"wins":int((rs>0).sum()),"win_rate":float((rs>0).mean()),
        "mean_net_r":float(rs.mean()),"median_net_r":float(np.median(rs)),
        "profit_factor_r":float(gains/losses) if losses>0 else 999999.0,
        "mean_net_return":float(np.mean([x.net_return for x in items])),
        "target_rate":sum(x.exit_reason=="TARGET" for x in items)/len(items),
        "stop_rate":sum(x.exit_reason=="STOP" for x in items)/len(items),
        "time_rate":sum(x.exit_reason=="TIME" for x in items)/len(items),
        "trades_per_calendar_day":len(items)/calendar_days,
    }


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--year",type=int,required=True); parser.add_argument("--cache",type=Path,required=True); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    all_items: list[Scored] = []; per_symbol={}
    days = 366 if pd.Timestamp(f"{args.year}-01-01").is_leap_year else 365
    for symbol in SYMBOLS:
        panel=load_symbol(symbol,args.year,args.cache); setups=detect_setups(symbol,panel,args.year); scored=[score_setup(s,panel) for s in setups]
        all_items.extend(scored); per_symbol[symbol]=stats(scored,days)
    global_items=global_single_position(all_items)
    result={
        "source":"prashanthaitha24/nq-strategy-b-bot Strategy B",
        "year":args.year,"cost_rate":COST_RATE,
        "transfer_defaults":{"min_gap_atr":MIN_GAP_ATR,"stop_buffer_atr":STOP_BUFFER_ATR,"htf_overlap_atr":HTF_OVERLAP_ATR,
                             "fvg5_max_age_hours":2,"fvg15_max_age_hours":24,"target_r":TARGET_R,"max_hold_minutes":MAX_HOLD_MINUTES},
        "per_symbol":per_symbol,"global_single_position":stats(global_items,days),
    }
    rows=[]
    for x in global_items:
        r=asdict(x.setup); r.update({"exit_ts":x.exit_ts,"exit_price":x.exit_price,"exit_reason":x.exit_reason,"gross_return":x.gross_return,"net_return":x.net_return,"net_r":x.net_r,"mfe":x.mfe,"mae":x.mae}); rows.append(r)
    pd.DataFrame(rows).to_csv(args.output/"global_trades.csv",index=False)
    (args.output/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))

if __name__=="__main__": main()
