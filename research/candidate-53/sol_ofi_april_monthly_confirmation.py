#!/usr/bin/env python3
"""Frozen SOL April-2024 true-L1 OFI confirmation using monthly BBO archives.

Economic policy is pinned in SOL_OFI_APRIL_FREEZE.md.  This file exists only
because Binance Vision preserved April-2024 bookTicker as a monthly archive
while the daily April endpoint is absent.  It changes the storage path, not the
OFI rule.  Both March warmup and April BBO archives are externally sorted by
original observed/transaction timestamp before OFI aggregation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path

import l1_ofi_participation_study as base
import bookticker_source_v3 as source
from bookticker_exact_order import iter_book_ticker_paths_exact

BINANCE_VISION = "https://data.binance.vision/data"


class SourceProxy:
    def __init__(self, record):
        self.kind=record.kind; self.source_url=record.source_url
        self.local_path=record.local_path; self.sha256=record.sha256
        self.size_bytes=record.size_bytes
        self.__dict__={
            "kind":record.kind,"source_url":record.source_url,
            "local_path":record.local_path,"sha256":record.sha256,
            "size_bytes":record.size_bytes,
        }


def download_sol(core_start: date, core_end: date, cache: Path):
    symbol="SOLUSDT"
    kline_start=core_start-timedelta(days=10)
    kline_end=core_end+timedelta(days=1)
    kline_paths=[]; book_paths=[]; evidence=[]
    d=kline_start
    while d<=kline_end:
        stamp=d.isoformat()
        url=f"{BINANCE_VISION}/futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{stamp}.zip"
        rec=SourceProxy(source.download_verified(url,cache/symbol/"klines","futures_kline"))
        kline_paths.append(Path(rec.local_path)); evidence.append(rec.__dict__)
        d+=timedelta(days=1)
    for month in ("2024-03","2024-04"):
        url=f"{BINANCE_VISION}/futures/um/monthly/bookTicker/{symbol}/{symbol}-bookTicker-{month}.zip"
        rec=SourceProxy(source.download_verified(url,cache/symbol/"bookTicker-monthly","book_ticker_monthly"))
        book_paths.append(Path(rec.local_path)); evidence.append(rec.__dict__)
    return kline_paths,book_paths,evidence


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--cache",type=Path,default=Path(".cache/c53-sol-ofi-april"))
    args=ap.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    start=date(2024,4,8); end=date(2024,4,14)
    klines,books,evidence=download_sol(start,end,args.cache)
    minutes=base.minute_frame(klines)
    # Replace only event iteration with exact chronological reconstruction.
    base.iter_book_ticker_paths=iter_book_ticker_paths_exact
    ofi=base.aggregate_minute_ofi(books)
    bars=base.participation_bars(minutes,ofi,start,end)
    outcomes=base.add_forward_outcomes(bars,minutes)
    q=0.90; horizon=240
    selected=base.tail(outcomes,q)
    nonoverlap=base.nonoverlap(selected,horizon)
    days=(end-start).days+1
    all_stats=base.stats(selected,"cont",horizon,days)
    non_stats=base.stats(nonoverlap,"cont",horizon,days)
    selected.to_csv(args.output/"selected_all.csv",index=False)
    nonoverlap.to_csv(args.output/"selected_nonoverlap.csv",index=False)
    result={
        "study":"Frozen SOL true-L1 OFI April monthly-BBO confirmation",
        "freeze":"SOL_OFI_APRIL_FREEZE.md",
        "symbol":"SOLUSDT","start":start.isoformat(),"end":end.isoformat(),
        "quantile":q,"horizon_minutes":horizon,"round_trip_cost_bps":base.ROUND_TRIP_COST_BPS,
        "participation_bars":int(len(outcomes)),"selected_events":int(len(selected)),
        "all_selected":all_stats,"nonoverlap":non_stats,
        "data_contract":"checksum-verified daily 1m klines + monthly Mar/Apr bookTicker exact external timestamp sort",
        "sources":evidence,
    }
    (args.output/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result,indent=2,sort_keys=True))

if __name__=="__main__": main()
