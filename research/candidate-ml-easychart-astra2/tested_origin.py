"""Secondary-test policy: root direction is a hypothesis, not a candle assertion.
The first rebound is observed, the opposing side tries again, and only a failed
second extension followed by breaking the rebound high/low creates an action.
The root stop and destination never move to the sensor's miniature candle.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from defended_origin import root_events,TICKS

FEATURES=['rr','cost_r','scale','sweep','deep_sweep','impulse_volume','impulse_flow','trend_hour','trend_fourhour','range_location','eff_hour','eff_fourhour','test_effort','test_result','acceptance','test_flow','relative_strength','market_direction','market_breadth']


def candidates(symbol,d,scales=(5,15,60)):
    events=root_events(symbol,d,scales); active=[]; result=[]
    o,h,l,c,v,buy=[d[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    ts=d.index.asi8; tick=TICKS[symbol]; delta=2*buy-v
    ar=np.abs(np.diff(np.r_[c[0],c])); accum=np.r_[0.,np.cumsum(ar)]
    vv=np.r_[0.,np.cumsum(v)]; dd=np.r_[0.,np.cumsum(delta)]
    tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1)
    micro=tr.rolling(30).median().shift().to_numpy()
    hlow=d.low.rolling(240).min().to_numpy(); hhigh=d.high.rolling(240).max().to_numpy()
    for i in range(241,len(d)):
        alive=[]
        for r in active:
            s=r.side; x=s*c[i]; adverse=l[i] if s==1 else -h[i]
            if (l[i]<=r.stop if s==1 else h[i]>=r.stop): continue
            if (h[i]>=r.target if s==1 else l[i]<=r.target): continue
            if r.touched<0 and l[i]<=r.high and h[i]>=r.low:
                r.touched=i; r.phase=0; r.first=adverse; r.bounce=x; r.test_start=i; r.first_end=i
            if r.touched<0: alive.append(r); continue
            if s*(c[i]-(r.high if s==1 else r.low))>r.atr: continue
            unit=max(micro[i],tick)
            if r.phase==0:
                r.first=min(r.first,adverse)
                if x-r.first>=2*unit and i>r.touched:
                    r.phase=1; r.bounce=x; r.first_end=i
                alive.append(r); continue
            if r.phase==1:
                r.bounce=max(r.bounce,x)
                if r.bounce-x>=unit:
                    r.phase=2; r.second=adverse; r.test_start=i
                alive.append(r); continue
            r.second=min(r.second,adverse)
            if r.second<=r.first:
                r.phase=0; r.first=r.second; r.touched=i
                alive.append(r); continue
            if x<=r.bounce or s*(c[i]-o[i])<=0:
                alive.append(r); continue
            risk=s*(c[i]-r.stop); reward=s*(r.target-c[i])
            if risk<=0 or reward<risk: alive.append(r); continue
            first_n=max(r.first_end-r.touched+1,1); second_n=max(i-r.test_start+1,1)
            first_effort=(vv[r.first_end+1]-vv[r.touched])/first_n
            second_effort=(vv[i+1]-vv[r.test_start])/second_n
            f=dict(r.features)
            f.update(rr=reward/risk,cost_r=c[i]*.0011/risk,range_location=(c[i]-hlow[i])/max(hhigh[i]-hlow[i],tick) if s==1 else (hhigh[i]-c[i])/max(hhigh[i]-hlow[i],tick),test_effort=second_effort/max(first_effort,1e-12),test_result=(r.second-r.first)/risk,acceptance=(x-r.first)/risk,test_flow=s*(dd[i+1]-dd[r.test_start])/max(vv[i+1]-vv[r.test_start],1e-12))
            for length,name in [(60,'hour'),(240,'fourhour')]:
                f['trend_'+name]=s*(c[i]-c[i-length])/r.atr
                f['eff_'+name]=s*(c[i]-c[i-length])/max(accum[i+1]-accum[i+1-length],tick)
            result.append(dict(symbol=symbol,side=s,ts=int(ts[i]),root_ts=r.time,source=r.source,entry=c[i],stop=r.stop,target=r.target,root_low=r.low,root_high=r.high,first_test=int(ts[r.touched]),second_test=int(ts[r.test_start]),**f))
            # One execution decision per root, never repeated ID fragments.
        active=alive
        for r in sorted(events.get(int(ts[i]),[]),key=lambda x:-x.scale):
            if not any(x.side==r.side and max(x.low,r.low)<=min(x.high,r.high) for x in active): active.append(r)
    return pd.DataFrame(result)


def market_context(rows,frames):
    out=rows.copy()
    if out.empty: return out
    closes=pd.concat({s:d.close for s,d in frames.items()},axis=1)
    ret=closes.pct_change(15)
    vol=closes.pct_change().rolling(240).std().shift()*np.sqrt(15)
    normalized=ret/vol.replace(0,np.nan)
    market=normalized.median(axis=1)
    for name in ['relative_strength','market_direction','market_breadth']: out[name]=0.
    for k,row in out.iterrows():
        t=pd.Timestamp(int(row.ts),tz='UTC'); s=row.side
        values=normalized.loc[t]
        out.at[k,'relative_strength']=float(s*(values[row.symbol]-market.loc[t]))
        out.at[k,'market_direction']=float(s*market.loc[t])
        out.at[k,'market_breadth']=float((s*values>0).mean())
    return out
