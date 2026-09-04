"""Compact chart-data views for examining decisions, not performance gates."""
from pathlib import Path
import pandas as pd
MINUTE=60_000_000_000

def write_cases(path,raws,trades,events,start_ns):
    cases=[]
    for r in trades.to_dict('records'):
        cases.append(dict(kind='TRADE',symbol=r['symbol'],ts=int(r['observed_time_ns']),event=r))
    missed=[r for r in events if r['ts']>=start_ns and r['reason']=='first_response_geometry_below_one_r'][:6]
    cases.extend(dict(kind='NO_TRADE',symbol=r['symbol'],ts=r['ts'],event=r) for r in missed)
    for k,item in enumerate(cases):
        ts=item['ts'];d=raws[item['symbol']].copy()
        d.index=pd.to_datetime(d.ts,utc=True)
        d['delta']=2*d.taker_buy_volume-d.volume
        columns={'open':'first','high':'max','low':'min','close':'last','volume':'sum','delta':'sum'}
        segments=[('CONTEXT 15m',ts-8*60*MINUTE,ts,15),('RESPONSE 1m',ts-20*MINUTE,ts+20*MINUTE,1)]
        text=[str(item['kind'])+' '+item['symbol']+' '+str(pd.Timestamp(ts,tz='UTC'))]
        info=item['event'];wanted=['side','source_level','entry','entry_fill','stop','target','net_r','holding_minutes','reason','event','interaction_time_ns','acceptance','return_depth','return_activity']
        text.extend(f'{key}: {info[key]}' for key in wanted if key in info)
        for title,a,b,tf in segments:
            q=d[(d.ts>a)&(d.ts<=b)]
            if tf>1:q=q.resample(f'{tf}min',label='right',closed='right').agg(columns).dropna()
            q=q[list(columns)].copy();q['delta_share']=q.delta/q.volume.replace(0,float('nan'))
            q=q.drop(columns='delta').round(4)
            text.append('\n'+title+'\n'+q.to_string())
        (path/f'case_{k:02d}.txt').write_text('\n'.join(text))
