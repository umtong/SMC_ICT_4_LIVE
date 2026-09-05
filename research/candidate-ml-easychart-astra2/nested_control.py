"""Nested counter-auction failure under a still-defended parent structure.
The price which produced a new adverse extreme remains the opposing control
point. A later tiny inside pivot cannot silently replace it. Entries are real
OB/FVG prices after that control point is defeated, not prices solved from R.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from market_io import aggregate
from channel_geometry import confirmed_pivots
from structural_context import ownership
from defended_origin import TICKS


def candidates(symbol,d,auction_minutes=15):
    ts=d.index.asi8
    o,h,l,c,v,buy=[d[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    micro=confirmed_pivots(d); turns=confirmed_pivots(aggregate(d,auction_minutes))
    parent=ownership(d,60).reindex(ts,method='ffill'); medium=ownership(d,15).reindex(ts,method='ffill')
    parent_owner=parent.owner.fillna(0).to_numpy(); parent_guard=parent.guard.to_numpy()
    medium_owner=medium.owner.fillna(0).to_numpy(); medium_guard=medium.guard.to_numpy()
    cumv=np.r_[0.,np.cumsum(v)]; cumb=np.r_[0.,np.cumsum(buy)]
    hist=[]; internal={}; micro_history=[]; opposite={}; active={}; seen=set(); rows=[]; tick=TICKS[symbol]
    for i in range(241,len(d)):
        t=int(ts[i]); owner=parent_owner[i] or medium_owner[i]
        guard=parent_guard[i] if parent_owner[i] else medium_guard[i]; keep={}
        for s,e in active.items():
            if owner!=s or not np.isfinite(guard): continue
            p,a,b=e['p'],e['a'],e['b']; ai=e['ai']; bi=e['bi']
            if s*(c[i]-guard)<0: continue
            if (h[i]>=b[1] if s==1 else l[i]<=b[1]): continue
            extreme=l[i] if s==1 else h[i]
            if s*(extreme-e['tip'])<0:
                e['tip']=extreme; e['tip_index']=i
                known=internal.get(s)
                if known is not None and known[0]>b[0]:
                    e['defended']=max(known[1],h[i]) if s==1 else min(known[1],l[i])
                    e['defended_ts']=known[0]
            width=s*(b[1]-a[1])
            if width<=0: continue
            if s*(e['tip']-a[1])<=width*.25: e['tested']=True
            failure=e['tested'] and s*(c[i]-e['defended'])>0 and s*(c[i]-a[1])>0 and s*(c[i]-o[i])>0
            if not failure:
                keep[s]=e; continue
            pi=e['pi']; ti=e['tip_index']; j=opposite.get(s,-1)
            if j<bi or j>=i: continue
            first_volume=cumv[ai+1]-cumv[pi]; test_volume=cumv[ti+1]-cumv[bi+1]
            first_buy=cumb[ai+1]-cumb[pi]; test_buy=cumb[ti+1]-cumb[bi+1]
            first_adverse=first_volume-first_buy if s==1 else first_buy
            test_adverse=test_volume-test_buy if s==1 else test_buy
            first_length=max(ai-pi+1,1); test_length=max(ti-bi,1)
            first_distance=abs(p[1]-a[1]); test_distance=abs(b[1]-e['tip'])
            effort=(test_volume/test_length)/max(first_volume/first_length,1e-12)
            progress=(test_distance/max(test_adverse,1e-12))/(first_distance/max(first_adverse,1e-12))
            # Low activity with greater price progress is thin opposing liquidity,
            # not exhausted aggression. A failed extension or weaker price impact
            # must accompany the subsequent defeat of the real control swing.
            if s*(e['tip']-a[1])<0 and progress>=1: continue
            stop=min(a[1],e['tip'])-tick if s==1 else max(a[1],e['tip'])+tick
            target=b[1]-s*tick
            zones=[('OB',min(o[j],c[j]),max(o[j],c[j]))]
            for z in range(max(j+2,ti+2),i+1):
                bodies=abs(c[z-1]-o[z-1])
                engulf=bodies>=2*max(abs(c[z-2]-o[z-2]),abs(c[z]-o[z]),tick)
                if engulf and s==1 and l[z]>h[z-2]: zones.append(('FVG',h[z-2],l[z]))
                if engulf and s==-1 and h[z]<l[z-2]: zones.append(('FVG',h[z],l[z-2]))
            eligible=[]
            for kind,lo,hi in zones:
                entry=hi if s==1 else lo; risk=s*(entry-stop); reward=s*(target-entry)
                if risk>0 and reward>=risk and s*(c[i]-entry)>tick: eligible.append((s*entry,kind,entry,risk,reward,lo,hi))
            if not eligible: continue
            _,kind,entry,risk,reward,lo,hi=max(eligible)
            total=cumv[i+1]-cumv[bi+1]; flow=s*(2*(cumb[i+1]-cumb[bi+1])/max(total,1e-12)-1)
            rows.append(dict(symbol=symbol,side=s,ts=t,root_ts=a[0],source=p[0],scale=float(auction_minutes),entry=entry,stop=stop,target=target,rr=reward/risk,entry_kind='limit',zone_kind=kind,root_low=lo,root_high=hi,pool=a[1],shift=e['defended'],flow=flow,delivery_minutes=i-bi,first_leg_minutes=first_length,test_effort=effort,test_progress_per_volume=progress,parent_guard=float(guard),rebound_ts=b[0],tip=e['tip'],tip_ts=int(ts[ti]),origin_ts=int(ts[j]),defended_ts=e['defended_ts']))
        active=keep
        if c[i]<o[i]: opposite[1]=i
        elif c[i]>o[i]: opposite[-1]=i
        if t in micro:
            internal[micro[t][2]]=micro[t]; micro_history.append((t,micro[t])); micro_history=micro_history[-1000:]
        if t not in turns: continue
        pivot=turns[t]
        if hist and hist[-1][2]==pivot[2]:
            if pivot[2]*(pivot[1]-hist[-1][1])>0: hist[-1]=pivot
            else: continue
        else: hist.append(pivot)
        hist=hist[-6:]
        if len(hist)<3: continue
        p,a,b=hist[-3:]; s=b[2]; key=(p[0],a[0],b[0])
        if key in seen or owner!=s or s*(b[1]-p[1])>=0: continue
        seen.add(key)
        pi=int(np.searchsorted(ts,p[0])); ai=int(np.searchsorted(ts,a[0])); bi=int(np.searchsorted(ts,b[0]))
        if ai<=pi or bi<=ai or bi>=i: continue
        segment=l[bi+1:i+1] if s==1 else h[bi+1:i+1]
        ti=bi+1+int(np.argmin(segment) if s==1 else np.argmax(segment)); tip=float(l[ti] if s==1 else h[ti])
        if (s==1 and np.max(h[bi+1:i+1])>=b[1]) or (s==-1 and np.min(l[bi+1:i+1])<=b[1]): continue
        known=[q for observed,q in micro_history if observed<=ts[ti] and q[2]==s and q[0]>b[0]]
        protected=known[-1] if known else b
        defended=max(protected[1],h[ti]) if s==1 else min(protected[1],l[ti])
        active[s]=dict(p=p,a=a,b=b,pi=pi,ai=ai,bi=bi,tip=tip,tip_index=ti,tested=False,defended=defended,defended_ts=protected[0])
    return pd.DataFrame(rows)
