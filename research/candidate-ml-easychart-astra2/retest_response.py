"""Observe origin contact, then price recovery through adverse execution VWAP.
The parent stop/target stay fixed. Awaiting hypotheses do not reserve account
capital; only the first selected, executable response becomes a position.
"""
import numpy as np
import pandas as pd
from control_transfer import candidates as origins
from defended_origin import TICKS

def candidates(symbol,d):
    plans=origins(symbol,d)
    if plans.empty: return plans
    ts=d.index.asi8
    o,h,l,c,v,buy,q,bq=[d[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume','quote_volume','buy_quote_volume']]
    result=[]; tick=TICKS[symbol]
    for r in plans.to_dict('records'):
        s=r['side']; stop=r['stop']; target=r['target']; contact=-1; volume=0.; quote=0.
        start=np.searchsorted(ts,r['ts'],side='right')
        for i in range(start,len(d)):
            invalid=(l[i]<=stop or h[i]>=target) if s==1 else (h[i]>=stop or l[i]<=target)
            if invalid: break
            if contact<0:
                if not (l[i]<=r['root_high'] and h[i]>=r['root_low']): continue
                contact=i
            av=v[i]-buy[i] if s==1 else buy[i]
            aq=q[i]-bq[i] if s==1 else bq[i]
            volume+=max(av,0.); quote+=max(aq,0.)
            if i==contact or volume<=0: continue
            control_price=quote/volume
            reclaimed=s*(c[i]-control_price)>tick
            micro_break=c[i]>h[i-1] if s==1 else c[i]<l[i-1]
            if not reclaimed or not micro_break: continue
            risk=s*(c[i]-stop); reward=s*(target-c[i])
            if risk<=0 or reward<risk: continue
            row=dict(r)
            row.update(ts=int(ts[i]),entry=c[i],entry_kind='market',rr=reward/risk,contact_ts=int(ts[contact]),prior_plan_ts=r['ts'],aggressor_vwap=control_price,response_minutes=float(i-contact+1),response_flow=float(s*(2*buy[contact:i+1].sum()/max(v[contact:i+1].sum(),1e-12)-1)))
            result.append(row); break
    return pd.DataFrame(result,columns=None if result else list(plans.columns))
