"""A boundary response selects direction; a lower-frame candle only locates entry.
The stop includes the entire contact or retest. The target is the nearest live
opposing pivot or channel destination. Neither is an optimized fixed RR.
"""
import numpy as np
import pandas as pd
from market_io import aggregate
from channel_geometry import geometries,confirmed_pivots,MINUTE
from defended_origin import TICKS
from tested_origin import market_context
FEATURES=['rr','cost_r','scale','sweep','impulse_volume','impulse_flow','trend_hour','trend_fourhour','range_location','eff_hour','eff_fourhour','test_effort','test_result','acceptance','test_flow','relative_strength','market_direction','market_breadth','width_atr','slope_atr','overshoot','reaction_volume']


def candidates(symbol,d,scales=(15,60)):
    b=aggregate(d,5); channels=geometries(d,scales); piv=confirmed_pivots(b)
    o,h,l,c,v,buy=[b[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    ts=b.index.asi8; active=[]; levels=[]; rows=[]; tick=TICKS[symbol]
    accum=np.r_[0.,np.cumsum(np.abs(np.diff(np.r_[c[0],c])))]
    vv=np.r_[0.,np.cumsum(v)]; dd=np.r_[0.,np.cumsum(2*buy-v)]
    med=b.volume.rolling(48).median().shift().to_numpy()
    hlow=b.low.rolling(48).min().to_numpy(); hhigh=b.high.rolling(48).max().to_numpy()
    for i in range(49,len(b)):
        t=int(ts[i]); levels=[p for p in levels if (h[i]<p[1] if p[2]==1 else l[i]>p[1])]
        alive=[]
        for ch in active:
            lower,upper=ch.edges(t); boundary=lower if ch.main==1 else upper
            toward=-ch.main
            outside=toward*(c[i]-boundary)>0
            touch=l[i]<=boundary if toward==-1 else h[i]>=boundary
            if ch.touched<0:
                if not touch: alive.append(ch); continue
                ch.touched=i
            ch.low=min(ch.low,l[i]); ch.high=max(ch.high,h[i])
            opposite=upper if toward==-1 else lower
            if not outside and (h[i]>=opposite if toward==-1 else l[i]<=opposite): continue
            if ch.accepted<0 and t%(ch.scale*MINUTE)==0 and outside: ch.accepted=i
            side=0; stop=0.; phase=''
            if ch.accepted>=0 and i>ch.accepted and toward*(o[i]-boundary)>0:
                if touch and outside:
                    if ch.retest<0: ch.retest=i
                    ch.test_low=min(ch.test_low,l[i]); ch.test_high=max(ch.test_high,h[i])
                    if toward*(c[i]-o[i])>0 and toward*(c[i]-o[i-1])>0:
                        side=toward; stop=ch.test_low-tick if side==1 else ch.test_high+tick; phase='acceptance'
            if not outside and -toward*(c[i]-o[i])>0 and -toward*(c[i]-o[i-1])>0:
                side=-toward; stop=ch.low-tick if side==1 else ch.high+tick; phase='rejection'
            if not side: alive.append(ch); continue
            entry=c[i]; risk=side*(entry-stop)
            if risk<=0: continue
            if phase=='rejection':
                lo2,hi2=ch.edges(t+ch.travel*MINUTE)
                destination=hi2 if side==1 else lo2
            else: destination=boundary+side*ch.width*.5
            targets=[destination]+[p[1]-side*tick for p in levels if p[2]==side and side*(p[1]-entry)>tick]
            targets=[p for p in targets if side*(p-entry)>tick]
            if not targets: continue
            target=min(targets,key=lambda p:side*(p-entry)); reward=side*(target-entry)
            if reward<risk: alive.append(ch); continue
            a=ch.touched; elapsed=max(i-a+1,1)
            f={'scale':float(ch.scale),'rr':reward/risk,'cost_r':entry*.0011/risk,'sweep':float(phase=='rejection'),'deep_sweep':0.,'impulse_volume':v[a]/max(med[a],1e-12),'impulse_flow':side*(2*buy[a]/max(v[a],1e-12)-1),'test_effort':v[i]/max(v[a],1e-12),'test_result':side*(c[i]-c[a])/risk,'acceptance':side*(entry-boundary)/risk,'test_flow':side*(dd[i+1]-dd[a])/max(vv[i+1]-vv[a],1e-12),'width_atr':ch.width/ch.atr,'slope_atr':side*ch.slope*ch.scale/ch.atr,'overshoot':toward*((ch.high if toward==1 else ch.low)-boundary)/ch.width,'reaction_volume':(vv[i+1]-vv[a])/elapsed/max(med[i],1e-12),'range_location':(entry-hlow[i])/max(hhigh[i]-hlow[i],tick) if side==1 else (hhigh[i]-entry)/max(hhigh[i]-hlow[i],tick)}
            for length,name in [(12,'hour'),(48,'fourhour')]:
                f['trend_'+name]=side*(c[i]-c[i-length])/ch.atr
                f['eff_'+name]=side*(c[i]-c[i-length])/max(accum[i+1]-accum[i+1-length],tick)
            rows.append(dict(symbol=symbol,side=side,ts=t,root_ts=ch.born,source=ch.source,entry=entry,stop=stop,target=target,root_low=lower,root_high=upper,phase=phase,**f))
        active=alive
        if t in piv: levels.append(piv[t])
        for ch in channels.get(t,[]):
            if not any(x.source==ch.source and x.main==ch.main for x in active): active.append(ch)
    return pd.DataFrame(rows)
