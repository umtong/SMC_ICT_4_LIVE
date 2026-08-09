#!/usr/bin/env python3
"""Untouched 2024-03-25..31 confirmation of the already-frozen delayed OFI policy.

The economic policy is exactly SOL_OFI_DELAYED_ACCEPTANCE_FREEZE.md. This runner
uses the preserved daily bookTicker archives (rather than the questionable
monthly archive) and exact chronological reconstruction. No rule is selected
from this interval.
"""
from __future__ import annotations

import argparse,json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd

import l1_ofi_participation_study as base
import bookticker_source_v3 as source
from bookticker_exact_order import iter_book_ticker_paths_exact

COST=21.0
START=date(2024,3,25)
END=date(2024,3,31)

class Proxy:
    def __init__(self,r):
        self.kind=r.kind; self.source_url=r.source_url; self.local_path=r.local_path; self.sha256=r.sha256; self.size_bytes=r.size_bytes
        self.__dict__={'kind':r.kind,'source_url':r.source_url,'local_path':r.local_path,'sha256':r.sha256,'size_bytes':r.size_bytes}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--cache',type=Path,default=Path('.cache/c53-sol-ofi-mar25')); a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    original=source.download_verified
    source.download_verified=lambda *args,**kwargs: Proxy(original(*args,**kwargs))
    base.iter_book_ticker_paths=iter_book_ticker_paths_exact
    k,b,evidence=base.download_symbol('SOLUSDT',START,END,a.cache)
    minutes=base.minute_frame(k); ofi=base.aggregate_minute_ofi(b); bars=base.participation_bars(minutes,ofi,START,END); outcomes=base.add_forward_outcomes(bars,minutes)
    q90=base.tail(outcomes,0.90); episodes=base.nonoverlap(q90,240).sort_values('entry_ts').copy()
    accepted=episodes[pd.to_numeric(episodes['cont_gross_bps_60'],errors='coerce').gt(COST)].copy()
    accepted['delayed_gross_bps']=pd.to_numeric(accepted['cont_gross_bps_240'])-pd.to_numeric(accepted['cont_gross_bps_60'])
    accepted['delayed_net_bps']=accepted['delayed_gross_bps']-COST
    accepted.to_csv(a.output/'accepted.csv',index=False); episodes.to_csv(a.output/'episodes.csv',index=False)
    v=accepted['delayed_gross_bps'].dropna().to_numpy(dtype=float); gains=v[v>0].sum() if len(v) else 0.; losses=-v[v<0].sum() if len(v) else 0.
    stats={'trades':int(len(v)),'trades_per_day':len(v)/7,'mean_gross_bps':float(v.mean()) if len(v) else 0.,'mean_net_bps':float(v.mean()-COST) if len(v) else 0.,'median_gross_bps':float(np.median(v)) if len(v) else 0.,'hit_rate':float(np.mean(v>0)) if len(v) else 0.,'cost_clear_rate':float(np.mean(v>COST)) if len(v) else 0.,'gross_pf':float(gains/losses) if losses>0 else (999999. if gains>0 else 0.)}
    result={'study':'Frozen delayed SOL true-L1 OFI untouched daily-BBO confirmation','freeze':'SOL_OFI_DELAYED_ACCEPTANCE_FREEZE.md','start':START.isoformat(),'end':END.isoformat(),'q90_events':int(len(q90)),'nonoverlap_episodes':int(len(episodes)),'accepted_events':int(len(v)),'stats':stats,'sources':evidence}
    (a.output/'summary.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,indent=2,sort_keys=True))

if __name__=='__main__':main()
