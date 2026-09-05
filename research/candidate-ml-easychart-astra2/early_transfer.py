"""External 15/60m liquidity, internal 1m transfer, structural OB/FVG entry.
Do not wait for a five-minute destination to be delivered merely to confirm a
one-minute turn. The nearest unconsumed 5m-or-larger pool remains the target.
Entry zones are real observed structures, never prices manufactured from RR.
"""
import numpy as np
import pandas as pd
from market_io import aggregate
from channel_geometry import confirmed_pivots
from defended_origin import TICKS

def candidates(symbol,d):
    ts=d.index.asi8
    o,h,l,c,v,bv=[d[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    internal=confirmed_pivots(d); external={}
    for scale in (5,15,60):
        for t,p in confirmed_pivots(aggregate(d,scale)).items(): external.setdefault(t,[]).append((*p,scale))
    pools=[]; latest={}; opposite={}; active={}; rows=[]; tick=TICKS[symbol]
    median=d.volume.rolling(60).median().shift().to_numpy()
    for i in range(241,len(d)):
        t=int(ts[i]); kept=[]
        for pt,price,kind,scale in pools:
            taken=h[i]>price if kind==1 else l[i]<price
            if not taken: kept.append((pt,price,kind,scale)); continue
            if scale<15: continue
            side=-kind
            if side not in latest or side not in opposite or side*(c[i-1]-latest[side][1])>=0: continue
            old=active.get(side)
            if old is None or scale>old['scale']:
                active[side]=dict(start=i,source=pt,pool=price,scale=scale,shift=latest[side][1],tip=l[i] if side==1 else h[i])
        pools=kept; waiting={}
        for s,e in active.items():
            a=e['start']; e['tip']=min(e['tip'],l[i]) if s==1 else max(e['tip'],h[i])
            shifted=s*(c[i]-e['shift'])>0 and s*(c[i]-e['pool'])>0 and s*(c[i]-o[i])>0
            if not shifted:
                if -s*(c[i]-e['pool'])<=abs(e['shift']-e['pool']): waiting[s]=e
                continue
            j=opposite[s]
            if j<a-1: continue
            stop=min(e['tip'],l[j])-tick if s==1 else max(e['tip'],h[j])+tick
            targets=[p[1]-s*tick for p in pools if p[2]==s and s*(p[1]-c[i])>tick]
            if not targets: continue
            target=min(targets,key=lambda price:s*(price-c[i]))
            zones=[('OB',min(o[j],c[j]),max(o[j],c[j]))]
            body=abs(c[i-1]-o[i-1])
            if body>0 and body>=2*max(abs(c[i-2]-o[i-2]),abs(c[i]-o[i])):
                if s==1 and l[i]>h[i-2]: zones.append(('FVG',h[i-2],l[i]))
                if s==-1 and h[i]<l[i-2]: zones.append(('FVG',h[i],l[i-2]))
            eligible=[]
            for kind,lo,hi in zones:
                entry=hi if s==1 else lo; risk=s*(entry-stop); reward=s*(target-entry)
                if risk>0 and reward>=risk and s*(c[i]-entry)>tick:
                    eligible.append((entry,kind,lo,hi,reward/risk))
            if not eligible: continue
            entry,kind,lo,hi,rr=max(eligible,key=lambda z:s*z[0])
            amount=v[a:i+1].sum(); flow=s*(2*bv[a:i+1].sum()/max(amount,1e-12)-1)
            rows.append(dict(symbol=symbol,side=s,ts=t,root_ts=int(ts[a]),source=e['source'],scale=float(e['scale']),entry=entry,stop=stop,target=target,rr=rr,root_low=lo,root_high=hi,entry_kind='limit',zone_kind=kind,pool=e['pool'],shift=e['shift'],delivery_minutes=(i-a+1),flow=flow,event_effort=amount/(i-a+1)/max(median[i],1e-12),shift_ts=t,origin_ts=int(ts[j])))
        active=waiting
        if c[i]<o[i]: opposite[1]=i
        elif c[i]>o[i]: opposite[-1]=i
        if t in internal: latest[internal[t][2]]=internal[t]
        for p in external.get(t,[]):
            existing=next((k for k,q in enumerate(pools) if q[0]==p[0] and q[2]==p[2]),None)
            if existing is None: pools.append(p)
            elif p[3]>pools[existing][3]: pools[existing]=p
    return pd.DataFrame(rows)
