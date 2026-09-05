"""Causal context: a trend is owned by the swing which produced a new extreme.
A tiny opposite candle does not reverse it. A new extreme ratchets the defended
swing; a close through that defended swing transfers control. This is a research
formalization of structure, not a rule specified numerically in EasyChart.
"""
import numpy as np
import pandas as pd
from market_io import aggregate
from channel_geometry import confirmed_pivots

FEATURES=['rr','cost_fraction','scale','owner_15','owner_60','owner_240','space_15','space_60','space_240','flow_15','flow_60','flow_240','return_15','return_60','return_240','resilience_15','resilience_60','resilience_240','delivery_flow','delivery_minutes','body_distance','stop_width','pool_age']

def ownership(d,minutes):
    b=aggregate(d,minutes); events=confirmed_pivots(b)
    high=None; low=None; guard=None; owner=0; rows=[]
    for stamp,r in b.iterrows():
        t=int(stamp.value); changed=False
        if owner==1 and guard is not None and r.close<guard:
            owner=-1; guard=high[1] if high is not None else r.high; changed=True
        elif owner==-1 and guard is not None and r.close>guard:
            owner=1; guard=low[1] if low is not None else r.low; changed=True
        if not changed:
            if high is not None and r.close>high[1]:
                owner=1
                if low is not None: guard=low[1]
                high=None
            elif low is not None and r.close<low[1]:
                owner=-1
                if high is not None: guard=high[1]
                low=None
        rows.append((t,owner,guard if guard is not None else r.close))
        if t in events:
            p=events[t]
            if p[2]==1: high=p
            else: low=p
    return pd.DataFrame(rows,columns=['ts','owner','guard']).set_index('ts')

def features(plans,frames):
    out=plans.copy()
    if out.empty: return out
    for symbol,d in frames.items():
        ix=out.index[out.symbol==symbol]
        if len(ix)==0: continue
        times=out.loc[ix,'ts'].to_numpy(dtype=np.int64); side=out.loc[ix,'side'].to_numpy(float)
        k=np.searchsorted(d.index.asi8,times,side='right')-1
        price=d.close.to_numpy(float); vol=d.volume.to_numpy(float); delta=2*d.buy_volume.to_numpy(float)-vol
        tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1)
        scale=tr.rolling(240).mean().shift().to_numpy()
        for n in (15,60,240):
            own=ownership(d,n); j=np.searchsorted(own.index.to_numpy(),times,side='right')-1
            state=own.iloc[np.maximum(j,0)]
            out.loc[ix,f'owner_{n}']=side*state.owner.to_numpy()
            out.loc[ix,f'space_{n}']=side*(price[k]-state.guard.to_numpy())/np.maximum(scale[k]*np.sqrt(n),1e-12)
            flow=pd.Series(delta,index=d.index).rolling(n).sum()/d.volume.rolling(n).sum().clip(lower=1e-12)
            ret=(d.close-d.close.shift(n))/(tr.rolling(240).mean().shift()*np.sqrt(n))
            ff=flow.to_numpy()[k]; rr=ret.to_numpy()[k]
            out.loc[ix,f'flow_{n}']=side*ff
            out.loc[ix,f'return_{n}']=side*rr
            # Price progress despite opposing executed pressure: an observation,
            # not evidence of a named institution or unobserved resting orders.
            out.loc[ix,f'resilience_{n}']=side*(rr-ff)
        risk=(out.loc[ix,'entry']-out.loc[ix,'stop']).abs()
        out.loc[ix,'cost_fraction']=out.loc[ix,'entry']*.0009/risk
        out.loc[ix,'stop_width']=risk.to_numpy()/np.maximum(scale[k],1e-12)
        out.loc[ix,'body_distance']=side*(price[k]-out.loc[ix,'entry'].to_numpy())/risk.to_numpy()
        out.loc[ix,'pool_age']=(out.loc[ix,'root_ts']-out.loc[ix,'source'])/60000000000
    out['delivery_flow']=out['flow']
    return out
