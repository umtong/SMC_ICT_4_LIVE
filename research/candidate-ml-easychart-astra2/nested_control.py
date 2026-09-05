"""A failed counter-auction is not a new macro trend.

The parent control swing is retained. A first adverse leg and its rebound
establish a local auction. A second adverse leg tests that auction; only its
observed failure can trigger an entry back toward the rebound's liquidity.
The rebound high/low, not a chosen R multiple, is the immutable destination.
This is a research hypothesis, not a profitable strategy claim.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from market_io import aggregate
from channel_geometry import confirmed_pivots
from structural_context import ownership
from defended_origin import TICKS


def candidates(symbol,d):
    ts=d.index.asi8
    o,h,l,c,v,buy=[d[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    micro=confirmed_pivots(d)
    turns=confirmed_pivots(aggregate(d,5))
    parent=ownership(d,60).reindex(ts,method='ffill')
    medium=ownership(d,15).reindex(ts,method='ffill')
    parent_owner=parent.owner.fillna(0).to_numpy(); parent_guard=parent.guard.to_numpy()
    medium_owner=medium.owner.fillna(0).to_numpy(); medium_guard=medium.guard.to_numpy()
    cumv=np.r_[0.,np.cumsum(v)]; cumb=np.r_[0.,np.cumsum(buy)]
    hist=[]; internal={}; active={}; seen=set(); rows=[]; tick=TICKS[symbol]
    for i in range(241,len(d)):
        t=int(ts[i]); owner=parent_owner[i] or medium_owner[i]
        guard=parent_guard[i] if parent_owner[i] else medium_guard[i]
        keep={}
        for s,e in active.items():
            # Parent control, not a lower-timeframe colour, invalidates this thesis.
            if owner!=s or not np.isfinite(guard): continue
            p,a,b=e['p'],e['a'],e['b']; ai=e['ai']; bi=e['bi']
            if s*(c[i]-guard)<0: continue
            if (h[i]>=b[1] if s==1 else l[i]<=b[1]): continue
            extreme=l[i] if s==1 else h[i]
            if s*(extreme-e['tip'])<0: e['tip']=extreme; e['tip_index']=i
            width=s*(b[1]-a[1])
            if width<=0: continue
            if s*(e['tip']-a[1])<=width*.25: e['tested']=True
            trigger=internal.get(s)
            changed=trigger is not None and trigger[0]>b[0] and s*(c[i]-trigger[1])>0
            failure=e['tested'] and changed and s*(c[i]-a[1])>0 and s*(c[i]-o[i])>0
            if not failure:
                keep[s]=e; continue
            first_start=e['pi']; first_volume=cumv[ai+1]-cumv[first_start]
            test_volume=cumv[i+1]-cumv[bi+1]
            first_length=max(ai-first_start+1,1); test_length=max(i-bi,1)
            first_distance=abs(p[1]-a[1]); test_distance=abs(b[1]-e['tip'])
            # Compare attacks as paths. A weak second attempt may have reduced
            # participation OR reduced directional progress per unit effort.
            effort=(test_volume/test_length)/max(first_volume/first_length,1e-12)
            progress=(test_distance/max(test_volume,1e-12))/(first_distance/max(first_volume,1e-12))
            weak_attack=effort<1 or progress<1
            if not weak_attack: continue
            stop=(min(a[1],e['tip'])-tick) if s==1 else (max(a[1],e['tip'])+tick)
            entry=c[i]; target=b[1]-s*tick; risk=s*(entry-stop); reward=s*(target-entry)
            if risk<=0 or reward<risk: continue
            flow=s*(2*(cumb[i+1]-cumb[bi+1])/max(test_volume,1e-12)-1)
            rows.append(dict(symbol=symbol,side=s,ts=t,root_ts=a[0],source=p[0],scale=60.,entry=entry,stop=stop,target=target,rr=reward/risk,entry_kind='market',root_low=min(stop,entry),root_high=max(stop,entry),pool=a[1],shift=trigger[1],flow=flow,delivery_minutes=test_length,first_leg_minutes=first_length,test_effort=effort,test_progress_per_volume=progress,parent_guard=float(guard),rebound_ts=b[0],tip=e['tip'],tip_ts=int(ts[e['tip_index']])))
        active=keep
        if t in micro: internal[micro[t][2]]=micro[t]
        if t not in turns: continue
        pivot=turns[t]
        if hist and hist[-1][2]==pivot[2]:
            if pivot[2]*(pivot[1]-hist[-1][1])>0: hist[-1]=pivot
            else: continue
        else: hist.append(pivot)
        hist=hist[-6:]
        if len(hist)<3: continue
        p,a,b=hist[-3:]; s=b[2]
        key=(p[0],a[0],b[0])
        if key in seen or owner!=s or s*(b[1]-p[1])>=0: continue
        seen.add(key)
        pi=int(np.searchsorted(ts,p[0])); ai=int(np.searchsorted(ts,a[0])); bi=int(np.searchsorted(ts,b[0]))
        if ai<=pi or bi<=ai: continue
        tip=float(np.min(l[bi+1:i+1])) if s==1 else float(np.max(h[bi+1:i+1]))
        if (s==1 and np.max(h[bi+1:i+1])>=b[1]) or (s==-1 and np.min(l[bi+1:i+1])<=b[1]): continue
        active[s]=dict(p=p,a=a,b=b,pi=pi,ai=ai,bi=bi,tip=tip,tip_index=i,tested=False)
    return pd.DataFrame(rows)
