#!/usr/bin/env python3
"""One-pass storage-optimized execution of two already-frozen SOL April policies.

Policies are independently pinned in:
- SOL_OFI_APRIL_FREEZE.md (direct q90 -> 240m continuation diagnostic)
- SOL_OFI_DELAYED_ACCEPTANCE_FREEZE.md (q90 -> 60m acceptance >21bp -> remaining 180m)

This file only avoids sorting the full March/April monthly BBO history twice.
No economic threshold or outcome definition is altered.
"""
from __future__ import annotations

import argparse,json
from datetime import date,datetime,timezone
from pathlib import Path
import numpy as np
import pandas as pd

import l1_ofi_participation_study as base
from bookticker_exact_order_range import iter_book_ticker_paths_exact_range
from sol_ofi_april_monthly_confirmation import download_sol

COST=21.0


def ns(day:date)->int:
    return int(datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc).timestamp()*1e9)


def direct_stats(frame:pd.DataFrame,days:int):
    return base.stats(frame,'cont',240,days)


def delayed_stats(frame:pd.DataFrame,days:int):
    accepted=frame[pd.to_numeric(frame['cont_gross_bps_60'],errors='coerce').gt(COST)].copy()
    v=(pd.to_numeric(accepted['cont_gross_bps_240'])-pd.to_numeric(accepted['cont_gross_bps_60'])).dropna().to_numpy(dtype=float)
    gains=v[v>0].sum() if len(v) else 0.; losses=-v[v<0].sum() if len(v) else 0.
    stats={
        'trades':int(len(v)),'trades_per_day':float(len(v)/days),
        'mean_gross_bps':float(v.mean()) if len(v) else 0.,
        'mean_net_bps':float(v.mean()-COST) if len(v) else 0.,
        'median_gross_bps':float(np.median(v)) if len(v) else 0.,
        'hit_rate':float(np.mean(v>0)) if len(v) else 0.,
        'cost_clear_rate':float(np.mean(v>COST)) if len(v) else 0.,
        'gross_pf':float(gains/losses) if losses>0 else (999999. if gains>0 else 0.),
    }
    return accepted,v,stats


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--cache',type=Path,default=Path('.cache/c53-sol-ofi-april-fast')); a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    start=date(2024,4,8); end=date(2024,4,14); days=7
    klines,books,evidence=download_sol(start,end,a.cache); minutes=base.minute_frame(klines)
    range_start=ns(date(2024,3,29)); range_end=ns(date(2024,4,15))
    base.iter_book_ticker_paths=lambda paths: iter_book_ticker_paths_exact_range(paths,range_start,range_end)
    ofi=base.aggregate_minute_ofi(books); bars=base.participation_bars(minutes,ofi,start,end); outcomes=base.add_forward_outcomes(bars,minutes)
    q90=base.tail(outcomes,0.90); episodes=base.nonoverlap(q90,240).sort_values('entry_ts').copy()
    accepted,vals,delay=delayed_stats(episodes,days)
    q90.to_csv(a.output/'q90_all.csv',index=False); episodes.to_csv(a.output/'q90_nonoverlap.csv',index=False); accepted.to_csv(a.output/'delayed_accepted.csv',index=False)
    result={
      'study':'One-pass frozen SOL April true-L1 OFI confirmations','symbol':'SOLUSDT','start':start.isoformat(),'end':end.isoformat(),'round_trip_cost_bps':COST,
      'data_contract':'monthly Mar/Apr BBO read fully, only 2024-03-29..2024-04-15 materialized then exact sorted; checksum verified',
      'participation_bars':int(len(outcomes)),'q90_events':int(len(q90)),'nonoverlap_episodes':int(len(episodes)),
      'direct_frozen':{'freeze':'SOL_OFI_APRIL_FREEZE.md','stats':direct_stats(episodes,days)},
      'delayed_frozen':{'freeze':'SOL_OFI_DELAYED_ACCEPTANCE_FREEZE.md','acceptance':'60m progress >21bp','hold_after_entry_minutes':180,'stats':delay},
      'sources':evidence,
    }
    (a.output/'summary.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,indent=2,sort_keys=True))

if __name__=='__main__':main()
