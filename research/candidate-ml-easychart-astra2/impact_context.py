"""Price progress relative to executed effort, in comparable units.
Past-only two-factor impact estimation separates common-market repricing from
local aggressive flow. The observed residual is a sensor, not identified hidden
orders or a guaranteed permanent price component. Models decide its value.
"""
import numpy as np
import pandas as pd
from structural_context import ownership

FEATURES=['rr','scale','stop_bps','target_bps','test_effort','test_progress_per_volume','test_depth','reply_fraction','duration_ratio','owner_15','owner_60','owner_240','value_position','relative_60','relative_240','response_5','response_15','response_60','common_5','common_15','common_60','flow_5','flow_15','flow_60','impact_imbalance','activity_burst','reply_acceleration']


def enrich(plans,frames):
    out=plans.copy()
    if out.empty: return out
    prices=pd.concat({s:d.close for s,d in frames.items()},axis=1)
    returns=np.log(prices).diff()*10000
    for symbol,d in frames.items():
        ix=out.index[out.symbol==symbol]
        if len(ix)==0: continue
        n=np.searchsorted(d.index.asi8,out.loc[ix,'ts'].to_numpy(np.int64),side='right')-1
        s=out.loc[ix,'side'].to_numpy(float); r=returns[symbol]
        factor=returns.drop(columns=symbol).mean(axis=1)
        baseline=d.quote_volume.ewm(span=240,adjust=False,min_periods=60).mean().shift(1)
        q=(2*d.buy_quote_volume-d.quote_volume)/baseline
        def ew(x): return x.ewm(span=1440,adjust=False,min_periods=240).mean().shift(1)
        mr,mf,mq=ew(r),ew(factor),ew(q)
        vf=ew(factor*factor)-mf*mf; vq=ew(q*q)-mq*mq; cfq=ew(factor*q)-mf*mq
        crf=ew(r*factor)-mr*mf; crq=ew(r*q)-mr*mq
        det=(vf*vq-cfq*cfq).where(lambda z:z>1e-12)
        beta=(crf*vq-crq*cfq)/det; lam=((crq*vf-crf*cfq)/det).clip(lower=0)
        sigma=np.sqrt((ew(r*r)-mr*mr).clip(lower=1e-10))
        common=beta*(factor-mf); flow=lam*(q-mq)
        residual=(r-mr-common-flow)/sigma
        relative=(r-beta*factor)/sigma
        residual_market=r-mr-common
        pos=q.clip(lower=0); neg=q.clip(upper=0)
        buy_impact=ew(residual_market*pos)/ew(pos*pos).clip(lower=1e-10)
        sell_impact=ew(residual_market*neg)/ew(neg*neg).clip(lower=1e-10)
        out.loc[ix,'impact_imbalance']=s*((buy_impact-sell_impact)/sigma).to_numpy()[n]
        for length in (5,15,60):
            out.loc[ix,f'response_{length}']=s*(residual.rolling(length).sum()/np.sqrt(length)).to_numpy()[n]
            out.loc[ix,f'common_{length}']=s*((common/sigma).rolling(length).sum()/np.sqrt(length)).to_numpy()[n]
            out.loc[ix,f'flow_{length}']=s*((flow/sigma).rolling(length).sum()/np.sqrt(length)).to_numpy()[n]
        for length in (60,240):
            out.loc[ix,f'relative_{length}']=s*(relative.rolling(length).sum()/np.sqrt(length)).to_numpy()[n]
        vwap=d.quote_volume.rolling(240).sum()/d.volume.rolling(240).sum().clip(lower=1e-12)
        out.loc[ix,'value_position']=s*(np.log(d.close/vwap)*10000/(sigma*np.sqrt(240))).to_numpy()[n]
        out.loc[ix,'reply_acceleration']=s*(residual.rolling(3).mean()-residual.shift(3).rolling(12).mean()).to_numpy()[n]
        out.loc[ix,'activity_burst']=(d.quote_volume.rolling(5).mean()/baseline).to_numpy()[n]
        for length in (15,60,240):
            own=ownership(d,length)
            j=np.searchsorted(own.index.to_numpy(),out.loc[ix,'ts'].to_numpy(),side='right')-1
            vals=own.owner.to_numpy()[np.maximum(j,0)]
            out.loc[ix,f'owner_{length}']=s*vals
    out['stop_bps']=(out.entry-out.stop).abs()/out.entry*10000
    out['target_bps']=(out.target-out.entry).abs()/out.entry*10000
    out['duration_ratio']=out.delivery_minutes/out.first_leg_minutes.clip(lower=1)
    out[FEATURES]=out[FEATURES].replace([np.inf,-np.inf],np.nan)
    return out
