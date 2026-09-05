"""Ordered price/effort evidence, rather than another candle-pattern vote.

Research adaptation of level-two path signatures (Chevyrev & Kormilitzin,
arXiv:1603.03788) and the timing problem in Ning et al., arXiv:2309.16008.
Neither paper establishes an edge in these four outright perpetual markets.

The six antisymmetric second-level terms are computed directly with NumPy.
This is the degree-two Chen identity, not an alternative signature framework.
The iisignature package was inspected; higher-order machinery is unnecessary
for these six areas. Inputs contain completed minutes only. A path with equal
net price/flow but a different order of events need not have equal areas.
"""
from __future__ import annotations
from dataclasses import replace
import math
import numpy as np
from astra_policy import MINUTE

CHANNELS=('time','price','pressure','activity')
PAIRS=tuple((i,j) for i in range(4) for j in range(i+1,4))
PATH_KEYS=('progress','pressure','activity','variation','excursion','close_location')+tuple(
    f'area_{CHANNELS[i]}_{CHANNELS[j]}' for i,j in PAIRS)
WINDOWS=('15','60','event')
PATH_FEATURES=tuple(f'path_{w}_{k}' for w in WINDOWS for k in PATH_KEYS)
DYNAMIC_FEATURES=('distance_to_source','elapsed_auction','relative_wait','noise_to_risk','peer_progress_now','peer_flow_now')
CORE_FEATURES=('auction_rejection','source_scale','higher_strength','trigger_strength',
               'risk_bps','cost_r','penetration','event_flow','event_activity','overlap_width')
FEATURES=CORE_FEATURES+DYNAMIC_FEATURES+PATH_FEATURES+(
    'x_spot_flow_15','x_spot_flow_60','x_relative_move_15','x_relative_move_60',
    'x_oi_change_15','x_oi_change_60','x_premium','x_premium_change')
# columns: ts, open, high, low, close, quote_volume, taker_buy_quote_volume
COLUMNS=['ts','open','high','low','close','quote_volume','taker_buy_quote_volume']


def signed_areas(increments:np.ndarray)->np.ndarray:
    if increments.ndim!=2 or increments.shape[1]!=4:
        raise ValueError('path increments must be n by four')
    before=np.cumsum(increments,axis=0)-increments
    return np.array([.5*np.sum(before[:,i]*increments[:,j]-before[:,j]*increments[:,i])
                     for i,j in PAIRS],dtype=float)


def path_features(rows:np.ndarray,side:int,scale:float,baseline:float)->dict[str,float]:
    if len(rows)<2:return {k:float('nan') for k in PATH_KEYS}
    q=rows[:,5];total=float(q.sum())
    if total<=0 or scale<=0:raise ValueError('non-positive observation scale')
    close=rows[:,4];previous=np.r_[rows[0,1],close[:-1]]
    price=side*(close-previous)/scale
    pressure=side*(2*rows[:,6]-q)/total
    increments=np.column_stack((np.full(len(rows),1./len(rows)),price,pressure,q/total))
    high=float(rows[:,2].max());low=float(rows[:,3].min())
    result=dict(progress=float(price.sum()),pressure=float(pressure.sum()),
                activity=math.log1p(total/max(baseline*len(rows),1e-12)),
                variation=float(np.abs(price).sum()),excursion=(high-low)/scale,
                close_location=side*(2*(close[-1]-low)/max(high-low,scale*1e-8)-1))
    result.update({f'area_{CHANNELS[i]}_{CHANNELS[j]}':float(v)
                   for (i,j),v in zip(PAIRS,signed_areas(increments),strict=True)})
    return result


def describe(rows:np.ndarray,seed,now:int)->dict[str,float]:
    """Exactly the same bounded-history function can run offline or online."""
    if not len(rows) or int(rows[-1,0])!=now or np.any(rows[:,0]>now):
        raise ValueError('point-in-time path history is not synchronized')
    side=int(seed.side.value);price=float(rows[-1,4]);risk=side*(price-seed.stop)
    if risk<=0:raise ValueError('source invalidation already crossed')
    prior=rows[-241:-1]
    scale=max(float(np.median(prior[:,2]-prior[:,3])),price*1e-9)
    baseline=max(float(np.median(prior[:,5])),1e-12)
    f={}
    for key,n in (('15',15),('60',60)):
        f.update({f'path_{key}_{k}':v for k,v in path_features(rows[-n:],side,scale*math.sqrt(n),baseline).items()})
    event=rows[rows[:,0]>=max(seed.interaction_time_ns,now-240*MINUTE)]
    f.update({f'path_event_{k}':v for k,v in path_features(event,side,risk,baseline).items()})
    f.update(distance_to_source=side*(price-seed.source_level)/risk,
             elapsed_auction=math.log1p(max(0,now-seed.interaction_time_ns)/MINUTE),
             relative_wait=(now-seed.observed_time_ns)/max(seed.source_scale*MINUTE,MINUTE),
             noise_to_risk=scale/risk)
    return f


class PathTable:
    def __init__(self,raw):
        self.arrays={s:d[COLUMNS].to_numpy(dtype=float) for s,d in raw.items()}
        self.times={s:d.ts.to_numpy(dtype=np.int64) for s,d in raw.items()}
    def at(self,seed,now=None):
        now=seed.observed_time_ns if now is None else int(now)
        s=seed.symbol;i=int(np.searchsorted(self.times[s],now,side='right'))
        rows=self.arrays[s][max(0,i-1441):i]
        if len(rows)<241:raise ValueError('path state needs completed warmup history')
        f=describe(rows,seed,now);side=int(seed.side.value);peer_progress=[];peer_flow=[]
        for other,a in self.arrays.items():
            if other==s:continue
            j=int(np.searchsorted(self.times[other],now,side='right'))
            if j<241 or int(a[j-1,0])!=now:raise ValueError('peer state unavailable at common watermark')
            recent=a[j-15:j];prior=a[j-241:j-1]
            scale=max(float(np.median(prior[:,2]-prior[:,3])),float(recent[-1,4])*1e-9)
            peer_progress.append(side*(recent[-1,4]-recent[0,1])/(scale*math.sqrt(15)))
            peer_flow.append(side*float(np.sum(2*recent[:,6]-recent[:,5]))/max(float(recent[:,5].sum()),1e-12))
        f['peer_progress_now']=float(np.median(peer_progress));f['peer_flow_now']=float(np.median(peer_flow))
        return f
    def attach(self,plans):
        return [replace(p,features={**p.features,**self.at(p)}) for p in plans]


def test_order_information():
    a=np.array([[0.,1.,0.,0.],[0.,0.,1.,0.]])
    b=a[::-1]
    assert np.allclose(a.sum(0),b.sum(0))
    assert np.allclose(signed_areas(a),-signed_areas(b))
    # price-pressure area is +1/2 when price precedes pressure.
    assert abs(signed_areas(a)[3]-.5)<1e-12
    line=np.array([[.5,1.,2.,.5],[.5,1.,2.,.5]])
    assert np.max(np.abs(signed_areas(line)))<1e-12

if __name__=='__main__':test_order_information()
