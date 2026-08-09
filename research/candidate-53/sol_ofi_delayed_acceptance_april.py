#!/usr/bin/env python3
"""April confirmation for SOL_OFI_DELAYED_ACCEPTANCE_FREEZE.md.

Storage/data preparation is shared with the separately frozen direct-q90 April
confirmation.  No rule is selected from April outcomes here.
"""
from __future__ import annotations

import argparse, json, math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import l1_ofi_participation_study as base
from bookticker_exact_order import iter_book_ticker_paths_exact
from sol_ofi_april_monthly_confirmation import download_sol

COST_BPS=21.0
Q=0.90
EVENT_HORIZON=240
CONFIRM_MINUTES=60


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--cache',type=Path,default=Path('.cache/c53-sol-ofi-delayed-april')); a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    start=date(2024,4,8); end=date(2024,4,14); days=(end-start).days+1
    k,b,evidence=download_sol(start,end,a.cache)
    minutes=base.minute_frame(k); base.iter_book_ticker_paths=iter_book_ticker_paths_exact
    ofi=base.aggregate_minute_ofi(b); bars=base.participation_bars(minutes,ofi,start,end); out=base.add_forward_outcomes(bars,minutes)
    q90=base.tail(out,Q); episodes=base.nonoverlap(q90,EVENT_HORIZON).sort_values('entry_ts').copy()
    accepted=episodes[pd.to_numeric(episodes['cont_gross_bps_60'],errors='coerce').gt(COST_BPS)].copy()
    accepted['delayed_gross_bps']=pd.to_numeric(accepted['cont_gross_bps_240'])-pd.to_numeric(accepted['cont_gross_bps_60'])
    accepted['delayed_net_bps']=accepted['delayed_gross_bps']-COST_BPS
    accepted['delayed_hit']=accepted['delayed_gross_bps']>0
    accepted['delayed_cost_clear']=accepted['delayed_gross_bps']>COST_BPS
    accepted.to_csv(a.output/'accepted.csv',index=False); episodes.to_csv(a.output/'episodes.csv',index=False)
    v=accepted['delayed_gross_bps'].dropna().to_numpy(dtype=float)
    gains=v[v>0].sum() if len(v) else 0.0; losses=-v[v<0].sum() if len(v) else 0.0
    stats={
        'trades':int(len(v)),
        'trades_per_day':float(len(v)/days),
        'mean_gross_bps':float(v.mean()) if len(v) else 0.0,
        'mean_net_bps':float(v.mean()-COST_BPS) if len(v) else 0.0,
        'median_gross_bps':float(np.median(v)) if len(v) else 0.0,
        'hit_rate':float(np.mean(v>0)) if len(v) else 0.0,
        'cost_clear_rate':float(np.mean(v>COST_BPS)) if len(v) else 0.0,
        'gross_pf':float(gains/losses) if losses>0 else (999999.0 if gains>0 else 0.0),
    }
    result={
        'study':'Frozen delayed true-L1 OFI acceptance April confirmation',
        'freeze':'SOL_OFI_DELAYED_ACCEPTANCE_FREEZE.md','symbol':'SOLUSDT',
        'start':start.isoformat(),'end':end.isoformat(),'q90_events':int(len(q90)),
        'nonoverlap_240m_episodes':int(len(episodes)),'accepted_events':int(len(accepted)),
        'acceptance':'direction-normalized 60m progress > 21bp','delayed_hold_minutes':180,
        'round_trip_cost_bps':COST_BPS,'stats':stats,'sources':evidence,
    }
    (a.output/'summary.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result,indent=2,sort_keys=True))

if __name__=='__main__': main()
