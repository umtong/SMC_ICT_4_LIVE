"""Observed cash/perpetual repricing and position creation, not candle votes.

OI does not identify long versus short intent. We expose unsigned contract-count
changes jointly with signed price/flow response. Metrics are made available one
5-minute publication interval after their timestamp. Missing data remains NaN.
"""
from pathlib import Path
import numpy as np
import pandas as pd
import experiment
from astra_policy import MINUTE

EXTRA_FEATURES=tuple([f'x_{name}_{n}' for n in (15,60) for name in
    ('spot_move','relative_move','spot_flow','relative_flow','oi_change')]+
    ['x_index_basis','x_premium','x_premium_change','x_spot_activity','x_futures_activity'])
SIGNED=set(EXTRA_FEATURES)-{'x_oi_change_15','x_oi_change_60','x_spot_activity','x_futures_activity'}

class ExtraObservations:
    def __init__(self,month,raws):
        self.tables={}
        root=Path('extended_market')
        for symbol,raw in raws.items():
            f=raw.set_index('ts');t=f.index.to_numpy(dtype=np.int64)
            old=experiment.MARKET
            try:
                experiment.MARKET=root
                spot=experiment.load_bars(symbol,month,'spot').set_index('ts').reindex(f.index)
                index=experiment.load_bars(symbol,month,'indexPriceKlines').set_index('ts').reindex(f.index)
                premium=experiment.load_bars(symbol,month,'premiumIndexKlines').set_index('ts').reindex(f.index)
            finally:experiment.MARKET=old
            out=pd.DataFrame(index=f.index)
            logspot=np.log(spot.close.where(spot.close>0));logfuture=np.log(f.close)
            fdelta=2*f.taker_buy_volume-f.volume;sdelta=2*spot.taker_buy_volume-spot.volume
            for n in (15,60):
                out[f'x_spot_move_{n}']=1e4*logspot.diff(n)
                out[f'x_relative_move_{n}']=1e4*(logfuture.diff(n)-logspot.diff(n))
                ff=fdelta.rolling(n,min_periods=n).sum()/f.volume.rolling(n,min_periods=n).sum().replace(0,np.nan)
                sf=sdelta.rolling(n,min_periods=n).sum()/spot.volume.rolling(n,min_periods=n).sum().replace(0,np.nan)
                out[f'x_spot_flow_{n}']=sf
                out[f'x_relative_flow_{n}']=ff-sf
            out['x_index_basis']=1e4*(f.close/index.close-1)
            out['x_premium']=1e4*premium.close
            out['x_premium_change']=1e4*premium.close.diff(15)
            out['x_spot_activity']=np.log1p(spot.quote_volume.rolling(15).mean()/spot.quote_volume.rolling(240).mean().shift(15))
            out['x_futures_activity']=np.log1p(f.quote_volume.rolling(15).mean()/f.quote_volume.rolling(240).mean().shift(15))
            metrics=[]
            for path in sorted((root/'metrics'/symbol).glob(f'{symbol}-metrics-{month}-*.zip')):
                x=pd.read_csv(path,compression='zip',usecols=['create_time','sum_open_interest'])
                metrics.append(x)
            for n in (15,60):out[f'x_oi_change_{n}']=np.nan
            if metrics:
                m=pd.concat(metrics,ignore_index=True)
                m['available']=(pd.to_datetime(m.create_time,utc=True).astype('int64')+5*MINUTE)
                m=m.sort_values('available').drop_duplicates('available',keep='last')
                stamps=m.available.to_numpy(dtype=np.int64)
                oi=np.log(m.sum_open_interest.where(m.sum_open_interest>0).to_numpy(dtype=float))
                current=np.searchsorted(stamps,t,side='right')-1
                for n in (15,60):
                    prior=np.searchsorted(stamps,t-n*MINUTE,side='right')-1
                    valid=(current>=0)&(prior>=0)
                    ci=np.maximum(current,0);pi=np.maximum(prior,0)
                    valid&=(t-stamps[ci]<=10*MINUTE)&(t-n*MINUTE-stamps[pi]<=10*MINUTE)
                    out.loc[valid,f'x_oi_change_{n}']=1e4*(oi[ci[valid]]-oi[pi[valid]])
            out=out[list(EXTRA_FEATURES)].replace([np.inf,-np.inf],np.nan)
            self.tables[symbol]=(t,out.to_numpy(dtype=float))
    def at(self,symbol,ts,side,unit_bps):
        stamps,values=self.tables[symbol]
        i=np.searchsorted(stamps,ts,side='right')-1
        if i<0 or stamps[i]!=ts:raise ValueError('extra observation clock mismatch')
        result={}
        for k,v in zip(EXTRA_FEATURES,values[i],strict=True):
            if k in SIGNED:v*=side
            if '_move_' in k:v/=max(unit_bps,1e-6)
            result[k]=float(v)
        return result
