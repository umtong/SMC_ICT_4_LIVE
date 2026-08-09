#!/usr/bin/env python3
"""One-pass storage-optimized execution of two already-frozen SOL April policies.

Policies are independently pinned in:
- SOL_OFI_APRIL_FREEZE.md (direct q90 -> 240m continuation diagnostic)
- SOL_OFI_DELAYED_ACCEPTANCE_FREEZE.md (q90 -> 60m acceptance >21bp -> remaining 180m)

No economic threshold or outcome definition is altered.  Only April monthly BBO
is required: the frozen OFI builder needs three warmup days, so Apr-05 already
provides the full trailing-90 participation-bar warmup for an Apr-08 start.
"""
from __future__ import annotations

import argparse,json
from dataclasses import asdict
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import numpy as np
import pandas as pd

import l1_ofi_participation_study as base
import bookticker_source_v3 as source
from bookticker_exact_order_range import iter_book_ticker_paths_exact_range

COST=21.0
BINANCE_VISION="https://data.binance.vision/data"


def ns(day:date)->int:
    return int(datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc).timestamp()*1e9)


def days(start:date,end:date):
    x=start
    while x<=end:
        yield x; x+=timedelta(days=1)


def download_inputs(start:date,end:date,cache:Path):
    symbol="SOLUSDT"; klines=[]; books=[]; evidence=[]
    # 10 completed calendar days before core start gives 7d participation
    # threshold history plus the 3d OFI-tail warmup.
    for d in days(start-timedelta(days=10),end+timedelta(days=1)):
        stamp=d.isoformat(); url=f"{BINANCE_VISION}/futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{stamp}.zip"
        r=source.download_verified(url,cache/symbol/"klines","futures_kline"); klines.append(Path(r.local_path)); evidence.append(asdict(r))
    url=f"{BINANCE_VISION}/futures/um/monthly/bookTicker/{symbol}/{symbol}-bookTicker-2024-04.zip"
    r=source.download_verified(url,cache/symbol/"bookTicker-monthly","book_ticker_monthly"); books.append(Path(r.local_path)); evidence.append(asdict(r))
    return klines,books,evidence


def direct_stats(frame:pd.DataFrame,days_n:int):
    return base.stats(frame,'cont',240,days_n)


def delayed_stats(frame:pd.DataFrame,days_n:int):
    accepted=frame[pd.to_numeric(frame['cont_gross_bps_60'],errors='coerce').gt(COST)].copy()
    v=(pd.to_numeric(accepted['cont_gross_bps_240'])-pd.to_numeric(accepted['cont_gross_bps_60'])).dropna().to_numpy(dtype=float)
    gains=v[v>0].sum() if len(v) else 0.; losses=-v[v<0].sum() if len(v) else 0.
    stats={
        'trades':int(len(v)),'trades_per_day':float(len(v)/days_n),
        'mean_gross_bps':float(v.mean()) if len(v) else 0.,
        'mean_net_bps':float(v.mean()-COST) if len(v) else 0.,
        'median_gross_bps':float(np.median(v)) if len(v) else 0.,
        'hit_rate':float(np.mean(v>0)) if len(v) else 0.,
        'cost_clear_rate':float(np.mean(v>COST)) if len(v) else 0.,
        'gross_pf':float(gains/losses) if losses>0 else (999999. if gains>0 else 0.),
    }
    return accepted,stats


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--cache',type=Path,default=Path('.cache/c53-sol-ofi-april-fast')); a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    start=date(2024,4,8); end=date(2024,4,14); days_n=7
    klines,books,evidence=download_inputs(start,end,a.cache); minutes=base.minute_frame(klines)
    # Only three pre-core days are needed for the causal trailing-90 OFI tail.
    range_start=ns(date(2024,4,5)); range_end=ns(date(2024,4,15))
    base.iter_book_ticker_paths=lambda paths: iter_book_ticker_paths_exact_range(paths,range_start,range_end)
    ofi=base.aggregate_minute_ofi(books); bars=base.participation_bars(minutes,ofi,start,end); outcomes=base.add_forward_outcomes(bars,minutes)
    q90=base.tail(outcomes,0.90); episodes=base.nonoverlap(q90,240).sort_values('entry_ts').copy()
    accepted,delay=delayed_stats(episodes,days_n)
    q90.to_csv(a.output/'q90_all.csv',index=False); episodes.to_csv(a.output/'q90_nonoverlap.csv',index=False); accepted.to_csv(a.output/'delayed_accepted.csv',index=False)
    result={
      'study':'One-pass frozen SOL April true-L1 OFI confirmations','symbol':'SOLUSDT','start':start.isoformat(),'end':end.isoformat(),'round_trip_cost_bps':COST,
      'data_contract':'checksum-verified daily 1m klines + April monthly BBO; only Apr05-Apr15 events materialized then exact observed/transaction timestamp sort',
      'participation_bars':int(len(outcomes)),'q90_events':int(len(q90)),'nonoverlap_episodes':int(len(episodes)),
      'direct_frozen':{'freeze':'SOL_OFI_APRIL_FREEZE.md','stats':direct_stats(episodes,days_n)},
      'delayed_frozen':{'freeze':'SOL_OFI_DELAYED_ACCEPTANCE_FREEZE.md','acceptance':'60m progress >21bp','hold_after_entry_minutes':180,'stats':delay},
      'sources':evidence,
    }
    (a.output/'summary.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,indent=2,sort_keys=True))

if __name__=='__main__':main()
