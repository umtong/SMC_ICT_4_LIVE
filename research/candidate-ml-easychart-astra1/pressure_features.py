"""Causal observations which bar patterns cannot supply by themselves.

Hypothesis: a failed price move has a different implication when it is a
perpetual-specific displacement with position destruction versus when spot
transactions confirm continuing demand. Neither an OI fall nor a basis gap is
asserted to identify liquidations, hidden ownership, or manipulation by itself.

The price/flow signed-area features retain sequence information lost when a
whole episode is collapsed into one return and one delta. They are a research
translation of path-dependent response, not an EasyChart rule.

Native five-minute positioning snapshots are delayed a full five minutes before
use. All bar and normalizer joins use already-completed observations only.
"""
from pathlib import Path
import calendar
import numpy as np
import pandas as pd
from experiment import load_bars
from astra_policy import MINUTE
from data_re1_derivatives import load_day_metrics
from datetime import date

HORIZONS=(5,15,60,240)
PRESSURE_COLUMNS=(
    'basis_bps','basis_deviation_bps','basis_z','basis_spot_bps',
    *(f'basis_change_{n}' for n in (5,15,60)),
    *(f'{name}_{n}' for n in HORIZONS for name in
      ('spot_flow','perp_flow','spot_progress','perp_progress','flow_disagreement',
       'buy_value_distance','sell_value_distance','path_area','spot_activity','perp_activity')),
    *(f'{name}_{n}' for n in (5,15,60,240) for name in ('oi_change','oi_change_z')),
    'crowd_direction','large_account_direction','large_position_direction',
    'crowd_displacement','funding_known','positioning_age_minutes',
    'common_spot_progress_15','common_spot_progress_60','relative_spot_progress_15','relative_spot_progress_60',
    'basis_cross_market_residual','basis_cross_market_dispersion',
)
SIGNED={
    'basis_bps','basis_deviation_bps','basis_z','basis_spot_bps',
    *(f'basis_change_{n}' for n in (5,15,60)),
    *(f'{name}_{n}' for n in HORIZONS for name in
      ('spot_flow','perp_flow','spot_progress','perp_progress','flow_disagreement','buy_value_distance','sell_value_distance')),
    'crowd_direction','large_account_direction','large_position_direction','crowd_displacement','funding_known',
    'common_spot_progress_15','common_spot_progress_60','relative_spot_progress_15','relative_spot_progress_60',
    'basis_cross_market_residual',
}

def finite_divide(a,b):return a/np.maximum(np.abs(b),1e-12)

def features_for_symbol(tape,symbol):
    future=tape.raw[symbol].set_index('ts')
    spot=load_bars(symbol,tape.month,'spot').set_index('ts').reindex(future.index)
    index=load_bars(symbol,tape.month,'indexPriceKlines').set_index('ts').reindex(future.index)
    if spot.close.isna().any() or index.close.isna().any():raise ValueError(f'incomplete independent price observations: {symbol}')
    out=pd.DataFrame(index=future.index)
    basis=10000*np.log(future.close/index.close)
    basis_prior=basis.shift(1).rolling(360,min_periods=60)
    out['basis_bps']=basis
    out['basis_deviation_bps']=basis-basis_prior.mean()
    out['basis_z']=finite_divide(basis-basis_prior.mean(),basis_prior.std())
    out['basis_spot_bps']=10000*np.log(future.close/spot.close)
    for n in (5,15,60):out[f'basis_change_{n}']=basis-basis.shift(n)
    logprice=np.log(future.close)
    sigma=logprice.diff().shift(1).rolling(1440,min_periods=60).std()
    for n in HORIZONS:
        fv=future.volume.rolling(n,min_periods=n).sum()
        sv=spot.volume.rolling(n,min_periods=n).sum()
        fb=future.taker_buy_volume.rolling(n,min_periods=n).sum()
        sb=spot.taker_buy_volume.rolling(n,min_periods=n).sum()
        flowf=finite_divide(2*fb-fv,fv);flows=finite_divide(2*sb-sv,sv)
        out[f'spot_flow_{n}']=flows;out[f'perp_flow_{n}']=flowf
        out[f'flow_disagreement_{n}']=flowf-flows
        out[f'spot_progress_{n}']=finite_divide(np.log(spot.close/spot.close.shift(n)),sigma*np.sqrt(n))
        out[f'perp_progress_{n}']=finite_divide(logprice-logprice.shift(n),sigma*np.sqrt(n))
        fq=future.quote_volume.rolling(n,min_periods=n).sum()
        bbq=future.taker_buy_quote_volume.rolling(n,min_periods=n).sum()
        buy_value=finite_divide(bbq,fb);sell_value=finite_divide(fq-bbq,fv-fb)
        out[f'buy_value_distance_{n}']=finite_divide(future.close-buy_value,future.close*sigma*np.sqrt(n))
        out[f'sell_value_distance_{n}']=finite_divide(future.close-sell_value,future.close*sigma*np.sqrt(n))
        out[f'spot_activity_{n}']=finite_divide(sv,spot.volume.shift(1).rolling(1440,min_periods=60).mean()*n)
        out[f'perp_activity_{n}']=finite_divide(fv,future.volume.shift(1).rolling(1440,min_periods=60).mean()*n)
        # Translation-invariant signed area of log price and cumulative delta.
        p=logprice.to_numpy();d=(2*future.taker_buy_volume-future.volume).to_numpy()
        c=np.cumsum(d);p0=np.r_[p[0],p[:-1]];c0=np.r_[0.,c[:-1]]
        increments=p0*d-c0*(p-p0)
        cumul=np.r_[0.,np.cumsum(increments)]
        area=np.full(len(p),np.nan)
        j=np.arange(n,len(p));i=j-n
        local=cumul[j+1]-cumul[i+1]-p[i]*(c[j]-c[i])+c[i]*(p[j]-p[i])
        area[j]=local
        out[f'path_area_{n}']=finite_divide(area,fv.to_numpy()*sigma.to_numpy()*np.sqrt(n))
    y,m=map(int,tape.month.split('-'))
    metrics=pd.concat([load_day_metrics(symbol,date(y,m,d),Path('astra_control_cache/inventory-context'))
                       for d in range(1,calendar.monthrange(y,m)[1]+1)],ignore_index=True)
    metrics=metrics.sort_values('create_time').drop_duplicates('create_time')
    stamps=metrics.create_time.astype('int64').to_numpy()
    # pandas may store datetime64[us]; explicitly normalize to nanoseconds.
    stamps=pd.DatetimeIndex(metrics.create_time).as_unit('ns').asi8
    metric_frame=pd.DataFrame({'ts':stamps+5*MINUTE,'original_ts':stamps})
    oi=np.log(metrics.sum_open_interest.to_numpy())
    oi_series=pd.Series(oi)
    oi_sigma=oi_series.diff().shift(1).rolling(288,min_periods=24).std()
    for n in (5,15,60,240):
        k=n//5;change=oi_series-oi_series.shift(k)
        metric_frame[f'oi_change_{n}']=10000*change
        metric_frame[f'oi_change_z_{n}']=finite_divide(change,oi_sigma*np.sqrt(k))
    for name,column in (('crowd_direction','count_long_short_ratio'),('large_account_direction','count_toptrader_long_short_ratio'),('large_position_direction','sum_toptrader_long_short_ratio')):
        metric_frame[name]=np.log(metrics[column].to_numpy())
    metric_frame['crowd_displacement']=metric_frame.crowd_direction-metric_frame.crowd_direction.shift(1).rolling(288,min_periods=24).mean()
    joined=pd.merge_asof(pd.DataFrame({'ts':future.index.to_numpy()}),metric_frame,on='ts',direction='backward')
    for col in metric_frame.columns:
        if col not in ('ts','original_ts'):out[col]=joined[col].to_numpy()
    out['positioning_age_minutes']=(future.index.to_numpy()-joined.original_ts.to_numpy())/MINUTE
    stale=out.positioning_age_minutes>15
    out.loc[stale,[c for c in metric_frame if c not in ('ts','original_ts')]]=np.nan
    funding=[(t,r) for t,s,r in tape.funding if s==symbol]
    ff=pd.DataFrame(funding,columns=['ts','funding_known']).sort_values('ts')
    out['funding_known']=pd.merge_asof(pd.DataFrame({'ts':future.index.to_numpy()}),ff,on='ts',direction='backward').funding_known.to_numpy()
    return out.replace([np.inf,-np.inf],np.nan)

class PressureHistory:
    def __init__(self,tape):
        self.frames={s:features_for_symbol(tape,s) for s in tape.symbols}
        for s,f in self.frames.items():
            peers=[self.frames[k] for k in tape.symbols if k!=s]
            for n in (15,60):
                values=np.column_stack([p[f'spot_progress_{n}'].to_numpy() for p in peers])
                common=np.median(values,axis=1)
                f[f'common_spot_progress_{n}']=common
                f[f'relative_spot_progress_{n}']=f[f'spot_progress_{n}']-common
            values=np.column_stack([p.basis_deviation_bps.to_numpy() for p in peers])
            f['basis_cross_market_residual']=f.basis_deviation_bps-np.median(values,axis=1)
            f['basis_cross_market_dispersion']=np.std(values,axis=1)
    def attach(self,plans):
        for p in plans:
            frame=self.frames[p.symbol]
            if p.observed_time_ns not in frame.index:raise ValueError('plan is not on an observed feature watermark')
            row=frame.loc[p.observed_time_ns];side=int(p.side.value)
            for c in PRESSURE_COLUMNS:p.features[c]=float(row[c])*(side if c in SIGNED else 1.)
        return plans
