"""Direct short market experiments; each window is a separate diagnostic account."""
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
from market_io import prepare,bars,funding,months_between,SYMBOLS,aggregate
from auction_geometry import candidates,market_context,FEATURES
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
    for row in trades.head(8).to_dict('records'):
        q=aggregate(frames[row['symbol']],15)
        start=pd.Timestamp(row['root_ts'],tz='UTC')-pd.Timedelta(hours=2)
        end=pd.Timestamp(row['exit_ts'],tz='UTC')+pd.Timedelta(minutes=30)
        q=q.loc[start:end,['open','high','low','close','volume','buy_volume']].round(5)
        records.append({'trade':row,'candles_15m':json.loads(q.reset_index().to_json(orient='records',date_format='iso'))})
    return records

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',default='short'); ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True); summaries=[]
    for a,b in WINDOWS:
        print('BOUNDARY AUCTION',a,b,flush=True)
        rows,frames=observe(a,b); rows.to_csv(OUT/f'boundary_actions_{a}.csv',index=False)
        if rows.empty: print('NO CANDIDATES',flush=True); continue
        for name,mask in {'boundary_all':np.ones(len(rows),bool),'boundary_rejection':rows.phase=='rejection','boundary_acceptance':rows.phase=='acceptance'}.items():
            trades,summary=route(rows.loc[mask],utc(a),utc(b)); summary.update(window=a,policy=name,candidates=int(mask.sum())); summaries.append(summary)
            print(json.dumps(summary),flush=True)
            trades.to_csv(OUT/f'trades_{name}_{a}.csv',index=False)
            if name=='boundary_all':
                (OUT/f'boundary_cases_{a}.json').write_text(json.dumps(cases(trades,frames),indent=2,allow_nan=False))
                chosen=set(trades.ts) if len(trades) else set()
                missed=rows[(~rows.ts.isin(chosen))&(rows.net_r>0)].head(5)
                (OUT/f'boundary_missed_{a}.json').write_text(json.dumps(cases(missed,frames),indent=2,allow_nan=False))
        by=rows.assign(win=rows.net_r>0).groupby(['symbol','phase']).agg(n=('net_r','size'),win=('win','mean'),net_r=('net_r','mean'),rr=('rr','mean'),hold=('hold_minutes','mean'))
        print('COUNTERFACTUAL GEOMETRY, not account returns\n',by.to_string(),flush=True)
    (OUT/'boundary_results.json').write_text(json.dumps(summaries,indent=2,allow_nan=False)+'\n')
if __name__=='__main__': main()
