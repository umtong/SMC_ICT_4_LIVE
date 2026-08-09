#!/usr/bin/env python3
"""True L1 OFI on causal participation bars for Candidate 53.

This study is motivated by Tony Li (2026), who reports a strong delayed OFI
impact profile in CME Ether futures on dollar-volume bars, but it does not try
to guess unavailable optimized strategy parameters.  Instead it reconstructs
the reusable market mechanism with data contracts already solved inside this
project:

- Candidate03's checksum-verified official Binance Vision bookTicker archive;
- Candidate16's exact best-bid/best-ask event parser;
- completed 1m Binance USD-M klines for trade notional and forward prices.

Participation clock:
The external paper reports 25,546 OOS dollar-volume bars over roughly Jan-2024
to Apr-2026, i.e. about 30 bars/calendar-day.  We therefore set each day's
notional threshold before the day begins to the median total daily notional of
the previous seven complete UTC days divided by 30.  No result-dependent bar
size is searched.

OFI is the standard Cont-style top-of-book event contribution:
  bid up: +new bid size; bid same: delta size; bid down: -old bid size
  ask down: -new ask size; ask same: old-new size; ask up: +old ask size.
Contributions are summed inside each participation bar and normalized by its
mean displayed top-of-book depth.  The bar is tradeable only after its final
minute is complete; entry is the strictly next minute open.

Both continuation and contrarian hypotheses are reported because independent
Binance research found smoothed taker-flow continuation structurally weak. This
is a mechanism diagnostic, not an optimized strategy.  Tail thresholds are
causal trailing quantiles.  The project's current 21 bp round-trip hurdle is
subtracted. No matching, account, leverage, position sizing, PnL compounding or
NAV engine is implemented here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

import bookticker_source_v3 as source
from topbook_features import iter_book_ticker_paths

BINANCE_VISION = "https://data.binance.vision/data"
HORIZONS = (10, 30, 60, 240)
TAIL_QUANTILES = (0.50, 0.75, 0.90, 0.95, 0.975)
ROUND_TRIP_COST_BPS = 21.0
TARGET_BARS_PER_DAY = 30.0
VOLUME_LOOKBACK_DAYS = 7
OFI_WARMUP_DAYS = 3
TRAILING_BARS = 90
MIN_TRAILING_BARS = 45


def days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def date_to_ns(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)


def download_symbol(symbol: str, core_start: date, core_end: date, cache: Path):
    kline_start = core_start - timedelta(days=VOLUME_LOOKBACK_DAYS + OFI_WARMUP_DAYS)
    book_start = core_start - timedelta(days=OFI_WARMUP_DAYS)
    kline_paths: list[Path] = []
    book_paths: list[Path] = []
    evidence = []
    for day in days(kline_start, core_end + timedelta(days=1)):
        stamp = day.isoformat()
        url = f"{BINANCE_VISION}/futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{stamp}.zip"
        record = source.download_verified(url, cache / symbol / "klines", "futures_kline")
        kline_paths.append(Path(record.local_path)); evidence.append(record.__dict__)
    for day in days(book_start, core_end):
        stamp = day.isoformat()
        url = f"{BINANCE_VISION}/futures/um/daily/bookTicker/{symbol}/{symbol}-bookTicker-{stamp}.zip"
        record = source.download_verified(url, cache / symbol / "bookTicker", "book_ticker")
        book_paths.append(Path(record.local_path)); evidence.append(record.__dict__)
    return kline_paths, book_paths, evidence


def minute_frame(kline_paths: list[Path]) -> pd.DataFrame:
    rows = []
    previous = -1
    for path in sorted(kline_paths, key=lambda p: p.name):
        archive, reader = source.one_csv_reader(path)
        try:
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue
                open_ns = source.normalize_timestamp_ns(int(row[0]))
                minute_ns = open_ns // 60_000_000_000 * 60_000_000_000
                if minute_ns <= previous:
                    raise ValueError("kline minute duplicate/non-monotonic")
                previous = minute_ns
                rows.append((
                    minute_ns,
                    float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[7]),
                ))
        finally:
            archive.close()
    frame = pd.DataFrame(rows, columns=["minute_ns","open","high","low","close","notional"])
    frame["minute"] = pd.to_datetime(frame["minute_ns"], unit="ns", utc=True)
    return frame.set_index("minute_ns", drop=False)


def aggregate_minute_ofi(book_paths: list[Path]) -> pd.DataFrame:
    # Stream all quote events exactly once; retain only minute aggregates.
    rows = []
    current_minute = None
    ofi_sum = 0.0
    depth_sum = 0.0
    spread_sum = 0.0
    updates = 0
    first_mid = math.nan
    last_mid = math.nan
    previous = None

    def flush():
        nonlocal ofi_sum, depth_sum, spread_sum, updates, first_mid, last_mid
        if current_minute is not None and updates > 0:
            rows.append((
                int(current_minute), ofi_sum, depth_sum / updates, spread_sum / updates,
                updates, first_mid, last_mid,
            ))
        ofi_sum = 0.0; depth_sum = 0.0; spread_sum = 0.0; updates = 0
        first_mid = math.nan; last_mid = math.nan

    for record in iter_book_ticker_paths(book_paths):
        _, bid, bid_qty, ask, ask_qty, _, observed_ns = record
        minute_ns = observed_ns // 60_000_000_000 * 60_000_000_000
        if current_minute is None:
            current_minute = minute_ns
        elif minute_ns != current_minute:
            flush(); current_minute = minute_ns
        mid = (bid + ask) / 2.0
        if not math.isfinite(first_mid):
            first_mid = mid
        last_mid = mid
        depth_sum += bid_qty + ask_qty
        spread_sum += (ask - bid) / mid * 10_000.0
        updates += 1
        if previous is not None:
            prev_bid, prev_bid_qty, prev_ask, prev_ask_qty = previous
            if bid > prev_bid:
                bid_term = bid_qty
            elif bid == prev_bid:
                bid_term = bid_qty - prev_bid_qty
            else:
                bid_term = -prev_bid_qty
            if ask < prev_ask:
                ask_term = -ask_qty
            elif ask == prev_ask:
                ask_term = prev_ask_qty - ask_qty
            else:
                ask_term = prev_ask_qty
            ofi_sum += bid_term + ask_term
        previous = (bid, bid_qty, ask, ask_qty)
    flush()
    frame = pd.DataFrame(rows, columns=[
        "minute_ns","ofi_qty","mean_top_depth_qty","mean_spread_bps","quote_updates","first_mid","last_mid",
    ])
    if frame.empty or frame["minute_ns"].duplicated().any():
        raise ValueError("invalid bookTicker OFI minute aggregation")
    frame["ofi_depth_ratio"] = frame["ofi_qty"] / frame["mean_top_depth_qty"].replace(0.0, np.nan)
    frame["mid_ret_bps"] = np.log(frame["last_mid"] / frame["first_mid"]) * 10_000.0
    return frame.set_index("minute_ns", drop=False)


def daily_thresholds(minutes: pd.DataFrame) -> dict[date, float]:
    working = minutes.copy()
    working["day"] = working["minute"].dt.date
    totals = working.groupby("day", sort=True)["notional"].sum().to_dict()
    ordered = sorted(totals)
    output = {}
    for i, day in enumerate(ordered):
        prior_days = ordered[max(0, i - VOLUME_LOOKBACK_DAYS):i]
        values = [totals[d] for d in prior_days if totals[d] > 0.0]
        if len(values) >= VOLUME_LOOKBACK_DAYS:
            output[day] = float(median(values) / TARGET_BARS_PER_DAY)
    return output


def participation_bars(minutes: pd.DataFrame, ofi: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    thresholds = daily_thresholds(minutes)
    joined = minutes.join(ofi[["ofi_qty","mean_top_depth_qty","mean_spread_bps","quote_updates","mid_ret_bps"]], how="left")
    joined["ofi_qty"] = joined["ofi_qty"].fillna(0.0)
    joined["quote_updates"] = joined["quote_updates"].fillna(0.0)
    records = []
    acc = None
    for row in joined.itertuples():
        day = row.minute.date()
        threshold = thresholds.get(day)
        if threshold is None or threshold <= 0.0:
            continue
        if acc is None:
            acc = {
                "start_ns": int(row.minute_ns), "start_day": day, "threshold": threshold,
                "notional": 0.0, "ofi": 0.0, "depth_num": 0.0, "depth_den": 0.0,
                "spread_num": 0.0, "spread_den": 0.0, "updates": 0.0, "minutes": 0,
            }
        # If UTC day changed before residual crossed, discard residual. This keeps
        # each bar governed by a threshold known before its own day began.
        if day != acc["start_day"]:
            acc = {
                "start_ns": int(row.minute_ns), "start_day": day, "threshold": threshold,
                "notional": 0.0, "ofi": 0.0, "depth_num": 0.0, "depth_den": 0.0,
                "spread_num": 0.0, "spread_den": 0.0, "updates": 0.0, "minutes": 0,
            }
        acc["notional"] += float(row.notional)
        acc["ofi"] += float(row.ofi_qty) if math.isfinite(float(row.ofi_qty)) else 0.0
        q = float(row.quote_updates)
        depth = float(row.mean_top_depth_qty) if pd.notna(row.mean_top_depth_qty) else math.nan
        spread = float(row.mean_spread_bps) if pd.notna(row.mean_spread_bps) else math.nan
        if q > 0 and math.isfinite(depth) and depth > 0:
            acc["depth_num"] += depth * q; acc["depth_den"] += q
        if q > 0 and math.isfinite(spread):
            acc["spread_num"] += spread * q; acc["spread_den"] += q
        acc["updates"] += q; acc["minutes"] += 1
        if acc["notional"] >= acc["threshold"] and acc["depth_den"] > 0:
            end_ns = int(row.minute_ns)
            mean_depth = acc["depth_num"] / acc["depth_den"]
            records.append({
                "start_ns": acc["start_ns"], "end_ns": end_ns,
                "bar_minutes": acc["minutes"], "trade_notional": acc["notional"],
                "notional_threshold": acc["threshold"], "ofi_qty": acc["ofi"],
                "mean_top_depth_qty": mean_depth,
                "ofi_depth_ratio": acc["ofi"] / mean_depth,
                "mean_spread_bps": acc["spread_num"] / max(acc["spread_den"], 1.0),
                "quote_updates": acc["updates"],
            })
            acc = None
    bars = pd.DataFrame.from_records(records)
    if bars.empty:
        return bars
    bars["end_ts"] = pd.to_datetime(bars["end_ns"], unit="ns", utc=True)
    bars["start_ts"] = pd.to_datetime(bars["start_ns"], unit="ns", utc=True)
    bars["abs_ofi"] = bars["ofi_depth_ratio"].abs()
    rolling = bars["abs_ofi"].shift(1).rolling(TRAILING_BARS, min_periods=MIN_TRAILING_BARS)
    for q in TAIL_QUANTILES:
        bars[f"abs_ofi_q{int(q*1000):03d}"] = rolling.quantile(q)
    core_open = pd.Timestamp(start, tz="UTC"); core_close = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    return bars[(bars["end_ts"] >= core_open) & (bars["end_ts"] < core_close)].reset_index(drop=True)


def add_forward_outcomes(bars: pd.DataFrame, minutes: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars
    minute_lookup = minutes.set_index("minute_ns")
    rows = []
    for bar in bars.to_dict("records"):
        entry_ns = int(bar["end_ns"] + 60_000_000_000)
        if entry_ns not in minute_lookup.index:
            continue
        entry = float(minute_lookup.loc[entry_ns, "open"])
        side = 1 if float(bar["ofi_depth_ratio"]) > 0 else -1
        if side == 0 or not (math.isfinite(entry) and entry > 0):
            continue
        bar["entry_ns"] = entry_ns; bar["entry_ts"] = pd.to_datetime(entry_ns, unit="ns", utc=True)
        bar["entry_price"] = entry; bar["ofi_side"] = side
        for horizon in HORIZONS:
            future_ns = entry_ns + horizon * 60_000_000_000
            if future_ns not in minute_lookup.index:
                bar[f"cont_gross_bps_{horizon}"] = math.nan; bar[f"rev_gross_bps_{horizon}"] = math.nan
                continue
            future = float(minute_lookup.loc[future_ns, "open"])
            cont = side * math.log(future / entry) * 10_000.0
            bar[f"cont_gross_bps_{horizon}"] = cont
            bar[f"rev_gross_bps_{horizon}"] = -cont
        rows.append(bar)
    return pd.DataFrame.from_records(rows)


def tail(frame: pd.DataFrame, q: float) -> pd.DataFrame:
    threshold = frame[f"abs_ofi_q{int(q*1000):03d}"]
    return frame[threshold.notna() & frame["abs_ofi"].ge(threshold)].copy()


def nonoverlap(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    chosen=[]; free_at=None
    for idx,row in frame.sort_values("entry_ts",kind="stable").iterrows():
        ts=pd.Timestamp(row["entry_ts"])
        if free_at is not None and ts < free_at: continue
        chosen.append(idx); free_at=ts+pd.Timedelta(minutes=horizon)
    return frame.loc[chosen].copy()


def stats(frame: pd.DataFrame, hypothesis: str, horizon: int, calendar_days: int):
    col=f"{hypothesis}_gross_bps_{horizon}"
    values=pd.to_numeric(frame[col],errors="coerce").dropna().to_numpy(dtype=float)
    if values.size==0:
        return {"trades":0,"mean_gross_bps":0.0,"mean_net_bps":0.0,"hit_rate":0.0,"cost_clear_rate":0.0,"gross_pf":0.0,"trades_per_day":0.0}
    gains=values[values>0].sum(); losses=-values[values<0].sum()
    return {"trades":int(values.size),"mean_gross_bps":float(values.mean()),"mean_net_bps":float(values.mean()-ROUND_TRIP_COST_BPS),
            "hit_rate":float((values>0).mean()),"cost_clear_rate":float((values>ROUND_TRIP_COST_BPS).mean()),
            "gross_pf":float(gains/losses) if losses>0 else 999999.0,"trades_per_day":float(values.size/calendar_days)}


def summarize(bars: pd.DataFrame, start: date, end: date):
    days_count=(end-start).days+1
    result={"calendar_days":days_count,"participation_bars":len(bars),"target_bars_per_day":TARGET_BARS_PER_DAY,
            "round_trip_cost_bps":ROUND_TRIP_COST_BPS,"tails":{}}
    for q in TAIL_QUANTILES:
        qframe=tail(bars,q); qbranch={}
        for horizon in HORIZONS:
            hbranch={}
            for hyp in ("cont","rev"):
                direct=stats(qframe,hyp,horizon,days_count)
                direct["nonoverlap"]=stats(nonoverlap(qframe,horizon),hyp,horizon,days_count)
                hbranch[hyp]=direct
            qbranch[str(horizon)]=hbranch
        result["tails"][f"q{q:.3f}"]=qbranch
    return result


def main():
    p=argparse.ArgumentParser(); p.add_argument("--symbol",required=True); p.add_argument("--start",required=True); p.add_argument("--end",required=True); p.add_argument("--cache",type=Path,required=True); p.add_argument("--output",type=Path,required=True); args=p.parse_args()
    start=date.fromisoformat(args.start); end=date.fromisoformat(args.end); args.output.mkdir(parents=True,exist_ok=True)
    kpaths,bpaths,evidence=download_symbol(args.symbol,start,end,args.cache)
    minutes=minute_frame(kpaths); ofi=aggregate_minute_ofi(bpaths)
    bars=participation_bars(minutes,ofi,start,end); bars=add_forward_outcomes(bars,minutes)
    result=summarize(bars,start,end); result["symbol"]=args.symbol
    bars.to_csv(args.output/"bars.csv.gz",index=False,compression="gzip")
    (args.output/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    (args.output/"evidence.json").write_text(json.dumps(evidence,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))

if __name__=="__main__": main()
