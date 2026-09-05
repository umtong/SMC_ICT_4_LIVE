"""Short causal market experiments. No pass/fail or promotion machinery."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
from market_io import prepare,bars,funding,months_between,SYMBOLS
from tested_origin import candidates,market_context,FEATURES
from first_passage import label,route,attach_episodes

OUT=Path('research_results/candidate_ml_easychart_astra2')
WINDOWS=[('2024-08-03','2024-08-10'),('2025-08-10','2025-08-17'),('2025-11-17','2025-11-24')]

def utc(x): return pd.Timestamp(x,tz='UTC').isoformat()

def observe(start,end):
    warm=(pd.Timestamp(start,tz='UTC')-pd.Timedelta(days=7)).isoformat(); finish=utc(end)
    prepare(months_between(warm,finish))
    frames={s:bars(s,warm,finish) for s in SYMBOLS}
    marks={s:bars(s,warm,finish,'markPriceKlines') for s in SYMBOLS}
    fs={s:funding(s,warm,finish) for s in SYMBOLS}
    rows=pd.concat([candidates(s,frames[s]) for s in SYMBOLS],ignore_index=True)
    if rows.empty: return rows,frames
    rows=rows[rows.ts>=pd.Timestamp(utc(start)).value].copy()
    rows=market_context(rows,frames)
    rows=attach_episodes(rows,frames)
    return label(rows,frames,fs,marks),frames

def cases(trades,frames):
    records=[]
    if trades.empty: return records
    for row in trades.head(12).to_dict('records'):
        d=frames[row['symbol']]
        start=pd.Timestamp(row['root_ts'],tz='UTC')-pd.Timedelta(hours=2)
        end=pd.Timestamp(row['exit_ts'],tz='UTC')+pd.Timedelta(minutes=30)
        q=d.loc[start:end].resample('15min',closed='right',label='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','buy_volume':'sum'}).dropna().round(5)
        records.append({'trade':row,'candles_15m':json.loads(q.reset_index().to_json(orient='records',date_format='iso'))})
    return records

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',default='short'); ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True); summaries=[]
    for a,b in WINDOWS:
        print('SECONDARY TEST',a,b,flush=True)
        rows,frames=observe(a,b)
        rows.to_csv(OUT/f'tested_origins_{a}.csv',index=False)
        if rows.empty: print('NO CANDIDATES',flush=True); continue
        policies={'all_tested':np.ones(len(rows),bool),'tested_direction':rows.trend_hour>0,'tested_sweep':rows.sweep>0,'tested_contracting':(rows.test_effort<1)&(rows.trend_hour>0)}
        for name,mask in policies.items():
            trades,summary=route(rows.loc[mask],utc(a),utc(b))
            summary.update(window=a,policy=name,candidates=int(mask.sum())); summaries.append(summary)
            print(json.dumps(summary),flush=True)
            trades.to_csv(OUT/f'trades_{name}_{a}.csv',index=False)
            if name=='tested_direction': (OUT/f'tested_cases_{a}.json').write_text(json.dumps(cases(trades,frames),indent=2,allow_nan=False))
        by=rows.assign(win=rows.net_r>0).groupby(['symbol','scale']).agg(n=('net_r','size'),win=('win','mean'),net_r=('net_r','mean'),rr=('rr','mean'),hold=('hold_minutes','mean'))
        print('COUNTERFACTUAL GEOMETRY (not account returns)\n',by.to_string(),flush=True)
    (OUT/'secondary_test_results.json').write_text(json.dumps(summaries,indent=2,allow_nan=False)+'\n')

if __name__=='__main__':main()
