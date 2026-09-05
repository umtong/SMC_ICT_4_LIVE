"""External liquidity -> internal structure break -> original-price retest.
The incoming side must lose its last defended internal swing. The limit entry
is the displacement origin, stop the entire liquidity event, and target the
nearest unconsumed opposing pool. No fixed-RR target or partial execution.
"""
import numpy as np
import pandas as pd
from market_io import aggregate
from channel_geometry import confirmed_pivots,MINUTE
from defended_origin import TICKS
from tested_origin import market_context
FEATURES=['rr','cost_r','scale','sweep_depth','shift_size','event_effort','flow','flow_resilience','retracement','trend_hour','trend_fourhour','eff_hour','eff_fourhour','relative_strength','market_direction','market_breadth','event_minutes']

def candidates(symbol,d):
    b=aggregate(d,5); ts=b.index.asi8
    o,h,l,c,v,buy=[b[k].to_numpy(float) for k in ['open','high','low','close','volume','buy_volume']]
    internal=confirmed_pivots(b); external={}
    for scale in (15,60):
        for time,p in confirmed_pivots(aggregate(d,scale)).items(): external.setdefault(time,[]).append((*p,scale))
    atr=(b.high-b.low).ewm(span=24,adjust=False).mean().shift().to_numpy()
    med=b.volume.rolling(48).median().shift().to_numpy()
    accum=np.r_[0.,np.cumsum(np.abs(np.diff(np.r_[c[0],c])))]
    vv=np.r_[0.,np.cumsum(v)]; dd=np.r_[0.,np.cumsum(2*buy-v)]
    pools=[]; latest={}; active={}; rows=[]; tick=TICKS[symbol]
    for i in range(49,len(b)):
        t=int(ts[i]); untouched=[]
        for pool in pools:
            pt,price,kind,scale=pool
            taken=h[i]>price if kind==1 else l[i]<price
            if not taken: untouched.append(pool); continue
            side=-kind
            if side not in latest: continue
            swing=latest[side]
            if side*(c[i-1]-swing[1])>=0: continue
            old=active.get(side)
            if old is None or scale>old['scale']:
                active[side]={'start':i,'source':pt,'pool':price,'scale':scale,'shift':swing[1],'extreme':h[i] if side==-1 else l[i]}
        pools=untouched; keep={}
        for side,event in active.items():
            a=event['start']
            event['extreme']=min(event['extreme'],l[i]) if side==1 else max(event['extreme'],h[i])
            reclaimed=side*(c[i]-event['pool'])>0
            transferred=side*(c[i]-event['shift'])>0
            if not reclaimed or not transferred:
                if abs(event['extreme']-event['pool'])<=abs(event['shift']-event['pool']): keep[side]=event
                continue
            opposite=[j for j in range(a,i+1) if side*(c[j]-o[j])<0]
            if not opposite: opposite=[j for j in range(max(0,a-2),a) if side*(c[j]-o[j])<0]
            if not opposite: continue
            origin=opposite[-1]; entry=(o[origin]+c[origin])/2
            stop=event['extreme']-side*tick; risk=side*(entry-stop)
            if risk<=0 or side*(c[i]-entry)<=0: continue
            targets=[p[1]-side*tick for p in pools if p[2]==side and side*(p[1]-c[i])>tick]
            if not targets: continue
            target=min(targets,key=lambda x:side*(x-entry)); reward=side*(target-entry)
            if reward<risk: continue
            length=i-a+1; volume=vv[i+1]-vv[a]; delta=dd[i+1]-dd[a]
            f={'rr':reward/risk,'cost_r':entry*.0009/risk,'scale':float(event['scale']),'sweep_depth':abs(event['extreme']-event['pool'])/max(atr[i],tick),'shift_size':side*(c[i]-event['shift'])/max(atr[i],tick),'event_effort':volume/length/max(med[i],1e-12),'flow':side*delta/max(volume,1e-12),'flow_resilience':side*(c[i]-c[a-1])/max(atr[i],tick)-side*delta/max(volume,1e-12),'retracement':side*(c[i]-entry)/max(atr[i],tick),'event_minutes':float(length*5)}
            for bars,name in [(12,'hour'),(48,'fourhour')]:
                f['trend_'+name]=side*(c[i]-c[i-bars])/max(atr[i],tick)
                f['eff_'+name]=side*(c[i]-c[i-bars])/max(accum[i+1]-accum[i+1-bars],tick)
            rows.append(dict(symbol=symbol,side=side,ts=t,root_ts=int(ts[a]),source=event['source'],entry=entry,stop=stop,target=target,root_low=min(o[origin],c[origin]),root_high=max(o[origin],c[origin]),entry_kind='limit',pool=event['pool'],shift=event['shift'],**f))
        active=keep
        if t in internal: latest[internal[t][2]]=internal[t]
        for pool in external.get(t,[]):
            if not any(p[0]==pool[0] and p[2]==pool[2] for p in pools): pools.append(pool)
    return pd.DataFrame(rows)
