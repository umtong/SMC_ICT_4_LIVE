"""Sweep -> control transfer -> initial delivery -> first return.
The cause and full stop persist while delivery develops. Consumed opposing
liquidity remains an observation, not an automatic ban on all later responses.
"""
import pandas as pd
from market_io import aggregate
from channel_geometry import confirmed_pivots
from defended_origin import TICKS
from tested_origin import market_context

def candidates(symbol,d,scales=(15,60)):
    b=aggregate(d,5); ts=b.index.asi8
    o,h,l,c=[b[k].to_numpy(float) for k in ['open','high','low','close']]
    internal=confirmed_pivots(b); external={}
    for scale in scales:
        for time,p in confirmed_pivots(aggregate(d,scale)).items(): external.setdefault(time,[]).append((*p,scale))
    pools=[]; latest={}; opposite={}; active={}; rows=[]; tick=TICKS[symbol]
    for i in range(49,len(b)):
        t=int(ts[i]); untouched=[]
        for pt,price,kind,scale in pools:
            if not (h[i]>price if kind==1 else l[i]<price):
                untouched.append((pt,price,kind,scale)); continue
            s=-kind
            if s not in latest or s not in opposite or s*(c[i-1]-latest[s][1])>=0: continue
            old=active.get(s)
            if old is None or (old['phase']==0 and scale>old['scale']):
                destinations=[p[1] for p in pools if p[2]==s and p[3]>=scale and s*(p[1]-c[i-1])>0]
                active[s]=dict(phase=0,start=i,source=pt,pool=price,scale=scale,shift=latest[s][1],tip=h[i] if s==-1 else l[i],destinations=destinations,delivered=set())
        pools=untouched; keep={}
        for s,e in active.items():
            for price in e['destinations']:
                if (h[i]>price if s==1 else l[i]<price): e['delivered'].add(price)
            if e['phase']==0:
                e['tip']=min(e['tip'],l[i]) if s==1 else max(e['tip'],h[i])
                valid=s*(c[i]-e['pool'])>0 and s*(c[i]-e['shift'])>0 and s*(c[i]-o[i])>0
                if not valid:
                    if -s*(c[i]-e['pool'])<=abs(e['shift']-e['pool']): keep[s]=e
                    continue
                j=opposite[s]
                e.update(phase=1,origin=j,entry=max(o[j],c[j]) if s==1 else min(o[j],c[j]),stop=min(e['tip'],l[j])-tick if s==1 else max(e['tip'],h[j])+tick,peak=h[i] if s==1 else l[i],shift_index=i)
                keep[s]=e; continue
            if (l[i]<=e['stop'] if s==1 else h[i]>=e['stop']): continue
            e['peak']=max(e['peak'],h[i]) if s==1 else min(e['peak'],l[i])
            returning=s*(c[i]-o[i])<0 and s*(c[i]-c[i-1])<0
            if not returning: keep[s]=e; continue
            rejected=sum(s*(c[i]-price)<0 for price in e['delivered'])
            entry=e['entry']; stop=e['stop']; risk=s*(entry-stop)
            targets=[e['peak']]+[p[1]-s*tick for p in pools if p[2]==s and s*(p[1]-c[i])>tick]
            target=min(targets,key=lambda p:s*(p-entry)); reward=s*(target-entry)
            if risk<=0 or reward<risk or s*(c[i]-entry)<=tick: continue
            a=e['start']; j=e['origin']; q=b.iloc[a:i+1]
            flow=s*(2*q.buy_volume.sum()/max(q.volume.sum(),1e-12)-1)
            rows.append(dict(symbol=symbol,side=s,ts=t,root_ts=int(ts[a]),source=e['source'],scale=float(e['scale']),entry=entry,stop=stop,target=target,rr=reward/risk,root_low=min(o[j],c[j]),root_high=max(o[j],c[j]),entry_kind='limit',pool=e['pool'],shift=e['shift'],delivery_minutes=(i-a+1)*5,flow=flow,accepted_pools=len(e['delivered'])-rejected,rejected_pools=rejected,shift_ts=int(ts[e['shift_index']]),origin_ts=int(ts[j])))
        active=keep
        if c[i]<o[i]: opposite[1]=i
        elif c[i]>o[i]: opposite[-1]=i
        if t in internal: latest[internal[t][2]]=internal[t]
        for pool in external.get(t,[]):
            if not any(p[0]==pool[0] and p[2]==pool[2] for p in pools): pools.append(pool)
    return pd.DataFrame(rows)
