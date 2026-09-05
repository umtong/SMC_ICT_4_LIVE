"""Compact price/volume observations around actual decisions, not a scoring system."""
import json
from pathlib import Path
import pandas as pd
from market_io import bars,aggregate,SYMBOLS
ROOT=Path('research_results/candidate_ml_easychart_astra2')

def review(start,end,resolution='1-MINUTE'):
    prefix=ROOT/f'nautilus_transfer_{start}_{resolution}'
    plans=pd.read_csv(str(prefix)+'_plans.csv')
    trades=pd.read_csv(str(prefix)+'_trades.csv')
    frames={s:aggregate(bars(s,(pd.Timestamp(start,tz='UTC')-pd.Timedelta(days=7)),pd.Timestamp(end,tz='UTC')),5) for s in SYMBOLS}
    out=[]
    traded=set(zip(trades.symbol,trades.ts)) if len(trades) else set()
    examples=[('traded',r) for r in trades.to_dict('records')]
    # Untraded sampling is chronological, not selected using future winners.
    missed=plans[[((r.symbol,r.ts) not in traded) for r in plans.itertuples()]].head(8)
    examples += [('not_traded',r) for r in missed.to_dict('records')]
    for kind,r in examples:
        s=r['symbol']; p=plans[(plans.symbol==s)&(plans.ts==r['ts'])].iloc[0].to_dict()
        a=pd.Timestamp(int(p['root_ts']),tz='UTC')-pd.Timedelta(minutes=90)
        z=pd.Timestamp(int(r.get('exit_ts',r['ts'])),tz='UTC')+pd.Timedelta(minutes=60)
        z=min(z,a+pd.Timedelta(hours=8))
        q=frames[s].loc[a:z,['open','high','low','close','volume','buy_volume']]
        out.append({'kind':kind,'decision_time':str(pd.Timestamp(int(r['ts']),tz='UTC')),'plan':p,'execution':r,'candles_5m':json.loads(q.reset_index().round(6).to_json(orient='records',date_format='iso'))})
    (ROOT/f'transfer_cases_{start}_{resolution}.json').write_text(json.dumps(out,indent=2)+'\n')
    print('CASE COUNT',start,len(out),flush=True)
if __name__=='__main__':
    for a,b in [('2024-08-03','2024-08-10'),('2025-08-10','2025-08-17'),('2025-11-17','2025-11-24')]: review(a,b)
