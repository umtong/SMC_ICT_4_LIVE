"""One price-effort decision geometry on either side of a structural boundary.

P-A-B is observed causally. When B advances beyond P, the tested boundary is
P (accepted breakout); otherwise it is A (failed counter-auction). The earlier
generator excluded B beyond P and therefore omitted simple trend pullbacks.
The same response, intact test stop and observed B destination apply to both.
"""
import numpy as np
import pandas as pd
from channel_geometry import confirmed_pivots
from market_io import aggregate
from defended_origin import TICKS


def one_scale(symbol,d,scale):
    ts=d.index.asi8; o,h,l,c,v,buy=[d[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    micro=confirmed_pivots(d); turns=confirmed_pivots(aggregate(d,scale))
    cv=np.r_[0.,np.cumsum(v)]; cb=np.r_[0.,np.cumsum(buy)]
    active={}; internal={}; hist=[]; rows=[]; tick=TICKS[symbol]
    for i in range(241,len(d)):
        t=int(ts[i]); keep={}
        for s,e in active.items():
            p,a,b=e['p'],e['a'],e['b']; pi=e['pi']; ai=e['ai']; bi=e['bi']; pool=e['pool']
            if (h[i]>=b[1] if s==1 else l[i]<=b[1]): continue
            tip=l[i] if s==1 else h[i]
            if s*(tip-e['tip'])<0: e['tip']=tip; e['ti']=i
            if e['accepted'] and s*(e['tip']-a[1])<0: continue
            width=s*(b[1]-pool); trigger=internal.get(s)
            tested=s*(e['tip']-pool)<=width*.25
            replied=tested and trigger is not None and trigger[0]>b[0] and s*(c[i]-trigger[1])>0 and s*(c[i]-pool)>0 and s*(c[i]-o[i])>0
            if not replied:
                keep[s]=e; continue
            entry=c[i]; stop=min(pool,e['tip'])-tick if s==1 else max(pool,e['tip'])+tick
            target=b[1]-s*tick; risk=s*(entry-stop); reward=s*(target-entry)
            if risk<=0 or reward<risk: continue
            ti=e['ti']; av=cv[ai+1]-cv[pi]; ab=cb[ai+1]-cb[pi]; tv=cv[ti+1]-cv[bi+1]; tb=cb[ti+1]-cb[bi+1]
            adverse_a=av-ab if s==1 else ab; adverse_t=tv-tb if s==1 else tb
            first_distance=abs(p[1]-a[1]); test_distance=abs(b[1]-e['tip'])
            length_a=max(ai-pi+1,1); length_t=max(ti-bi,1)
            total=cv[i+1]-cv[bi+1]; flow=s*(2*(cb[i+1]-cb[bi+1])/max(total,1e-12)-1)
            rows.append(dict(symbol=symbol,side=s,ts=t,root_ts=e['root_ts'],source=p[0],scale=float(scale),entry=entry,stop=stop,target=target,rr=reward/risk,entry_kind='market',root_low=min(stop,entry),root_high=max(stop,entry),pool=pool,shift=trigger[1],flow=flow,delivery_minutes=i-bi,first_leg_minutes=length_a,test_effort=(tv/length_t)/max(av/length_a,1e-12),test_progress_per_volume=(test_distance/max(adverse_t,1e-12))/(first_distance/max(adverse_a,1e-12)),test_depth=s*(pool-e['tip'])/width,reply_fraction=s*(c[i]-e['tip'])/width,rebound_ts=b[0],tip=e['tip'],tip_ts=int(ts[ti]),accepted_break=float(e['accepted']),parent_progress=s*(b[1]-p[1])/max(first_distance,tick)))
        active=keep
        if t in micro: internal[micro[t][2]]=micro[t]
        if t not in turns: continue
        q=turns[t]
        if hist and hist[-1][2]==q[2]:
            if q[2]*(q[1]-hist[-1][1])>0: hist[-1]=q
            else: continue
        else: hist.append(q)
        hist=hist[-3:]
        if len(hist)!=3: continue
        p,a,b=hist; s=b[2]; accepted=s*(b[1]-p[1])>0; pool=p[1] if accepted else a[1]
        pi=int(np.searchsorted(ts,p[0])); ai=int(np.searchsorted(ts,a[0])); bi=int(np.searchsorted(ts,b[0]))
        if ai<=pi or bi<=ai or bi>=i or s*(b[1]-pool)<=0: continue
        if (h[bi+1:i+1].max()>=b[1] if s==1 else l[bi+1:i+1].min()<=b[1]): continue
        z=l[bi+1:i+1] if s==1 else h[bi+1:i+1]
        ti=bi+1+int(np.argmin(z) if s==1 else np.argmax(z))
        root_ts=a[0]
        if accepted:
            broken=np.flatnonzero(s*(c[ai+1:bi+1]-p[1])>0)
            if len(broken)==0: continue
            root_ts=int(ts[ai+1+broken[0]])
        active[s]=dict(p=p,a=a,b=b,pi=pi,ai=ai,bi=bi,pool=pool,accepted=accepted,root_ts=root_ts,tip=float(l[ti] if s==1 else h[ti]),ti=ti)
    return pd.DataFrame(rows)


def candidates(symbol,d):
    return pd.concat([one_scale(symbol,d,n) for n in (5,15)],ignore_index=True)
