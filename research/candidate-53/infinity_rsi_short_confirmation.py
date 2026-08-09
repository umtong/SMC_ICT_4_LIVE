#!/usr/bin/env python3
"""Frozen confirmation runner for the surviving INFINITY public RSI-short family.

Economic policy is pinned in INFINITY_RSI_SHORT_FREEZE.md. This file only
executes that unchanged rule on another calendar year and reports one global
single-position diagnostic path. Formal success remains NautilusTrader-only.
"""
from __future__ import annotations
import argparse, json
from dataclasses import asdict
from pathlib import Path
import pandas as pd
import infinitytrader_core_causal_study as base
import infinitytrader_core_causal_study_v2 as fixed


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,default=2024); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--cache',type=Path,default=Path('.cache/c53-infinity-confirm')); a=ap.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    days=366 if a.year%4==0 else 365; all_t=[]; per={}
    for s in base.SYMBOLS:
        panel=base.load_symbol(s,a.year,a.cache); sigs,h4,h1=fixed.detect(s,panel,a.year)
        sigs=[z for z in sigs if z.method=='rsi_short']
        trades=[]; free=pd.Timestamp(f'{a.year}-01-01',tz='UTC')
        for z in sigs:
            if z.entry_ts<free: continue
            t=base.score(z,panel,h4,h1)
            if t is None: continue
            trades.append(t); all_t.append(t); free=t.exit_ts+pd.Timedelta(minutes=1)
        per[s]={'signals':len(sigs),'summary':base.summary(trades,days)}
        pd.DataFrame([asdict(t) for t in trades]).to_csv(a.output/f'{s}.csv',index=False)
    glob=base.global_arbitrate(all_t,a.year); pd.DataFrame([asdict(t) for t in glob]).to_csv(a.output/'global.csv',index=False)
    result={'study':'Frozen INFINITY RSI-short confirmation','year':a.year,'policy':'INFINITY_RSI_SHORT_FREEZE.md','per_symbol':per,'global_one_position':base.summary(glob,days)}
    (a.output/'summary.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,indent=2,sort_keys=True))
if __name__=='__main__': main()
