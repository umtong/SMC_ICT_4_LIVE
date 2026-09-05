"""Intrabar price-effort failure at pre-existing structural liquidity.

A swept boundary is the first recovery destination, not a distant later high.
Ten-second observations time the response while the entire excursion extreme
remains the stop. Executed pressure is observed; hidden orders are not asserted.
This candidate is an unproven economic hypothesis, not a production strategy.
"""
from pathlib import Path
import numpy as np
import pandas as pd
from market_io import aggregate
from channel_geometry import confirmed_pivots
from structural_context import ownership
from defended_origin import TICKS


def candidates(symbol,d,execution_root):
    paths=sorted((Path(execution_root)/'1s'/symbol).glob('*.parquet'))
    if not paths: raise FileNotFoundError(symbol)
    seconds=pd.concat([pd.read_parquet(p) for p in paths]).sort_index()
    spec={'open':'first','high':'max','low':'min','close':'last','volume':'sum','buy_volume':'sum','quote_volume':'sum','buy_quote_volume':'sum'}
    sensor=seconds.resample('10s',closed='right',label='right',origin='epoch').agg(spec).dropna(subset=['close'])
    events={}
    for scale in (15,60):
        for t,p in confirmed_pivots(aggregate(d,scale)).items(): events.setdefault(t,[]).append((*p,scale))
    pools=[]; first=int(sensor.index[0].value)
    # Warm-up removes already consumed levels. A pivot cannot be reborn just
    # because the second-resolution archive starts later than the history.
    for stamp,r in d[d.index.asi8<first].iterrows():
        pools=[p for p in pools if not (r.high>p[1] if p[2]==1 else r.low<p[1])]
        for p in events.get(int(stamp.value),[]):
            if not any(p[0]==q[0] and p[2]==q[2] for q in pools): pools.append(p)
    ts=sensor.index.asi8; o,h,l,c,v,buy=[sensor[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    cv=np.r_[0.,np.cumsum(v)]; cb=np.r_[0.,np.cumsum(buy)]
    hourly=ownership(d,60); active={}; rows=[]; tick=TICKS[symbol]
    for i in range(1,len(sensor)):
        t=int(ts[i]); kept=[]; crossed={1:[],-1:[]}
        for p in pools:
            if (h[i]>p[1] if p[2]==1 else l[i]<p[1]): crossed[-p[2]].append(p)
            else: kept.append(p)
        pools=kept
        for s,new in crossed.items():
            if not new: continue
            if s not in active:
                active[s]={'start':i,'levels':[],'tip':l[i] if s==1 else h[i],'tip_index':i,'origin':o[i]}
            active[s]['levels'].extend(new)
        surviving={}
        for s,e in active.items():
            extreme=l[i] if s==1 else h[i]
            if s*(extreme-e['tip'])<0:
                e['tip']=extreme; e['tip_index']=i; e['origin']=o[i]
            destinations=[p for p in e['levels'] if s*(p[1]-c[i])>tick]
            if not destinations: continue
            ti=e['tip_index']; total=cv[i+1]-cv[ti]
            delta=s*(2*(cb[i+1]-cb[ti])-total)
            # Defeat the opening price of the thrust that made the extreme,
            # while cumulative aggressive pressure since that thrust still
            # points the other way. A candle colour alone is insufficient.
            response=s*(c[i]-e['origin'])>tick and s*(c[i]-c[i-1])>0 and delta<0
            if not response:
                surviving[s]=e; continue
            p=min(destinations,key=lambda p:s*(p[1]-c[i]))
            target=p[1]-s*tick; stop=e['tip']-s*tick; entry=c[i]
            risk=s*(entry-stop); reward=s*(target-entry)
            if risk<=0 or reward<risk:
                surviving[s]=e; continue
            j=np.searchsorted(hourly.index.to_numpy(),t,side='right')-1
            owner=int(hourly.owner.iloc[j]) if j>=0 else 0
            rows.append(dict(symbol=symbol,side=s,ts=t,root_ts=int(ts[e['start']]),source=p[0],scale=float(p[3]),entry=entry,stop=stop,target=target,rr=reward/risk,entry_kind='market',root_low=min(stop,entry),root_high=max(stop,entry),pool=p[1],shift=e['origin'],flow=delta/max(total,1e-12),delivery_minutes=(t-int(ts[e['start']]))/60000000000,tip=e['tip'],tip_ts=int(ts[ti]),owner_60=s*owner,probe_volume=total,probe_depth=abs(p[1]-e['tip'])/entry*10000))
        active=surviving
        for p in events.get(t,[]):
            if not any(p[0]==q[0] and p[2]==q[2] for q in pools): pools.append(p)
    return pd.DataFrame(rows)
