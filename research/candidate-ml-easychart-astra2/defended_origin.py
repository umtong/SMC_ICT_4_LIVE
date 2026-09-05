"""One market hypothesis, several observations: a defended origin and its first return.

Source-derived: OB body is the entry area; ALL creation wicks define invalidation;
5/15/60 minute context; a liquidity event strengthens an OB, not the reverse;
target is the observed delivery extremum. No institutional identity is inferred.
Research hypotheses: a 1m response senses a parent origin without moving its stop;
side-relative price/volume features describe trend, failed extension and retest.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
import numpy as np
import pandas as pd
from market_io import aggregate

TICKS={'BTCUSDT':.1,'ETHUSDT':.01,'SOLUSDT':.01,'XRPUSDT':.0001}
FEATURES=['rr','risk_atr','scale','sweep','deep_sweep','body_ratio','displacement','impulse_flow','impulse_volume','trend_hour','trend_fourhour','eff_hour','eff_fourhour','range_location','response_eff','pullback_volume','pullback_flow','test_flow','test_volume','retest_depth','retest_age','return_speed','micro_strength']

@dataclass
class Root:
    symbol:str
    side:int
    time:int
    scale:int
    low:float
    high:float
    stop:float
    target:float
    atr:float
    features:dict
    source:int
    touched:int=-1
    armed:int=-1
    used:bool=False


def root_events(symbol,d,scales=(15,60)):
    roots={}; tick=TICKS[symbol]
    for scale in scales:
        b=aggregate(d,scale)
        o,h,l,c,v=[b[k].to_numpy(float) for k in ['open','high','low','close','volume']]
        body=np.abs(c-o)
        tr=pd.concat([(b.high-b.low),(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1)
        atr=tr.ewm(span=max(8,240//scale),adjust=False,min_periods=8).mean().shift(1).to_numpy()
        low=b.low.rolling(8).min().shift(2).to_numpy(); high=b.high.rolling(8).max().shift(2).to_numpy()
        low4=b.low.rolling(32).min().shift(2).to_numpy(); high4=b.high.rolling(32).max().shift(2).to_numpy()
        vbase=b.volume.rolling(16).median().shift(1).to_numpy()
        t=b.index.asi8
        for j in range(34,len(b)):
            s=1 if c[j]>o[j] else -1
            if s*(c[j-1]-o[j-1])>=0: continue
            if body[j-1]<.1*atr[j] or body[j]<2*body[j-1] or body[j]<.6*atr[j]: continue
            if s*(c[j]-o[j-1])<=0: continue
            lo=min(l[j],l[j-1]); hi=max(h[j],h[j-1])
            sweep=int(lo<low[j] and c[j]>low[j]) if s==1 else int(hi>high[j] and c[j]<high[j])
            deep=int(lo<low4[j] and c[j]>low4[j]) if s==1 else int(hi>high4[j] and c[j]<high4[j])
            stop=lo-tick if s==1 else hi+tick
            target=h[j] if s==1 else l[j]
            zlo=min(o[j-1],c[j-1]); zhi=max(o[j-1],c[j-1])
            qv=b.buy_volume.iloc[j]
            f={'scale':float(scale),'sweep':float(sweep),'deep_sweep':float(deep),'body_ratio':body[j]/body[j-1], 'displacement':s*(c[j]-o[j-1])/atr[j], 'impulse_flow':s*(2*qv/max(v[j],1e-12)-1),'impulse_volume':v[j]/max(vbase[j],1e-12)}
            root=Root(symbol,s,int(t[j]),scale,zlo,zhi,stop,target,atr[j],f,int(t[j-1]))
            roots.setdefault(int(t[j]),[]).append(root)
    return roots


def candidates(symbol,d,scales=(15,60)):
    events=root_events(symbol,d,scales); active=[]; result=[]
    o,h,l,c,v,buy=[d[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    ts=d.index.asi8; n=len(d); minute=60000000000; tick=TICKS[symbol]
    delta=2*buy-v
    absret=np.abs(np.diff(np.r_[c[0],c])); accum=np.r_[0.,np.cumsum(absret)]
    vv=np.r_[0.,np.cumsum(v)]; dd=np.r_[0.,np.cumsum(delta)]
    hrange=d.high.rolling(240).max()-d.low.rolling(240).min()
    hlow=d.low.rolling(240).min().to_numpy()
    hv=hrange.to_numpy(); vmed=d.volume.rolling(60).median().to_numpy()
    for i in range(241,n):
        alive=[]
        for r in active:
            s=r.side
            # Hypothesis ends at its original invalidation/destination, NOT at a new tiny OB.
            if (l[i]<=r.stop if s==1 else h[i]>=r.stop): continue
            if (h[i]>=r.target+tick if s==1 else l[i]<=r.target-tick): continue
            if r.used: continue
            if r.touched<0 and l[i]<=r.high and h[i]>=r.low: r.touched=i
            if r.touched<0: alive.append(r); continue
            # A first return has an observable lifecycle, rather than a retry/time quota.
            away=(c[i]>r.high+r.atr if s==1 else c[i]<r.low-r.atr)
            if away: continue
            response=(c[i]>h[i-1] and c[i]>o[i]) if s==1 else (c[i]<l[i-1] and c[i]<o[i])
            if not response: alive.append(r); continue
            entry=c[i]; risk=s*(entry-r.stop); reward=s*(r.target-entry)
            if risk<=0 or reward<risk: alive.append(r); continue
            # Sensor must still be near the parent's original body, not chasing the expansion.
            if s*(entry-(r.high if s==1 else r.low))>r.atr*.35: alive.append(r); continue
            a=max(r.touched, i-60); age=max(1,i-a+1)
            f=dict(r.features)
            for length,name in [(60,'hour'),(240,'fourhour')]:
                distance=accum[i+1]-accum[i+1-length]
                f['trend_'+name]=s*(c[i]-c[i-length])/max(r.atr,1e-12)
                f['eff_'+name]=s*(c[i]-c[i-length])/max(distance,1e-12)
            rvol=vv[i+1]-vv[a]; rdelta=dd[i+1]-dd[a]
            f.update(rr=reward/risk,risk_atr=risk/r.atr,range_location=(c[i]-hlow[i])/max(hv[i],tick) if s==1 else 1-(c[i]-hlow[i])/max(hv[i],tick),response_eff=s*(c[i]-c[a-1])/max(accum[i+1]-accum[a],tick),pullback_volume=rvol/age/max(vmed[i],1e-12),pullback_flow=s*rdelta/max(rvol,1e-12),test_flow=s*delta[i]/max(v[i],1e-12),test_volume=v[i]/max(vmed[i],1e-12),retest_depth=s*(entry-r.stop)/max(abs(r.target-r.stop),tick),retest_age=(ts[i]-r.time)/minute/r.scale,return_speed=abs(c[i]-c[np.searchsorted(ts,r.time)])/max((ts[i]-r.time)/minute,1)/r.atr,micro_strength=s*(c[i]-o[i])/max(h[i]-l[i],tick))
            result.append(dict(symbol=symbol,side=s,ts=int(ts[i]),root_ts=r.time,source=r.source,entry=entry,stop=r.stop,target=r.target,root_low=r.low,root_high=r.high,**f))
            r.used=True
        active=alive
        # Completed higher-timeframe data only become available after the current minute.
        for r in sorted(events.get(int(ts[i]),[]),key=lambda x:-x.scale):
            overlap=any(x.side==r.side and max(x.low,r.low)<=min(x.high,r.high) for x in active)
            if not overlap: active.append(r)
    return pd.DataFrame(result)
