"""Cash/perpetual pressure observations with publication-lagged contract OI.

OI decline alone is not labelled liquidation or assigned a trading direction.
The learner observes cash demand, futures demand, premium, and contract-count
creation/destruction jointly. Missing observations remain NaN.
"""
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import calendar,hashlib,json,urllib.request
import numpy as np
import pandas as pd
import experiment
from astra_policy import MINUTE,SYMBOLS

EXTRA=('inventory_change_5','inventory_change_15','inventory_change_60','inventory_change_surprise',
       'spot_flow_15','relative_flow_15','spot_move_15','relative_spot_move_15',
       'premium_bps','premium_change_15','spot_participation','futures_participation')
UNSIGNED={'inventory_change_5','inventory_change_15','inventory_change_60',
          'inventory_change_surprise','spot_participation','futures_participation'}
ROOT=Path('extended_market')

def ensure_extra(months,output):
    jobs=[]
    for month in months:
        year,m=map(int,month.split('-'))
        for symbol in SYMBOLS:
            for typ in ('spot','premiumIndexKlines'):
                prefix='spot' if typ=='spot' else 'futures/um'
                folder='klines' if typ=='spot' else typ
                name=f'{symbol}-1m-{month}.zip'
                jobs.append((f'https://data.binance.vision/data/{prefix}/monthly/{folder}/{symbol}/1m/{name}',ROOT/typ/symbol/name))
            for day in range(1,calendar.monthrange(year,m)[1]+1):
                name=f'{symbol}-metrics-{year:04d}-{m:02d}-{day:02d}.zip'
                jobs.append((f'https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{name}',ROOT/'metrics'/symbol/name))
    def get(job):
        url,path=job;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            with urllib.request.urlopen(url,timeout=60) as response:payload=response.read()
            path.write_bytes(payload)
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    with ThreadPoolExecutor(max_workers=10) as pool:hashes=list(pool.map(get,jobs))
    (output/'inventory_inputs.json').write_text(json.dumps(hashes,indent=2))

class InventoryObservations:
    def __init__(self,month,raws):
        self.tables={}
        for symbol,raw in raws.items():
            f=raw.set_index('ts');times=f.index.to_numpy(dtype=np.int64)
            original=experiment.MARKET
            try:
                experiment.MARKET=ROOT
                spot=experiment.load_bars(symbol,month,'spot').set_index('ts').reindex(f.index)
                premium=experiment.load_bars(symbol,month,'premiumIndexKlines').set_index('ts').reindex(f.index)
            finally:experiment.MARKET=original
            out=pd.DataFrame(index=f.index)
            fflow=(2*f.taker_buy_volume-f.volume).rolling(15).sum()/f.volume.rolling(15).sum().replace(0,np.nan)
            sflow=(2*spot.taker_buy_volume-spot.volume).rolling(15).sum()/spot.volume.rolling(15).sum().replace(0,np.nan)
            out['spot_flow_15']=sflow;out['relative_flow_15']=fflow-sflow
            out['spot_move_15']=np.log(spot.close.where(spot.close>0)).diff(15)*10000
            out['relative_spot_move_15']=np.log(f.close).diff(15)*10000-out.spot_move_15
            out['premium_bps']=premium.close*10000
            out['premium_change_15']=premium.close.diff(15)*10000
            out['spot_participation']=np.log1p(spot.quote_volume.rolling(15).mean()/spot.quote_volume.rolling(240).mean().shift(15))
            out['futures_participation']=np.log1p(f.quote_volume.rolling(15).mean()/f.quote_volume.rolling(240).mean().shift(15))
            pieces=[pd.read_csv(path,compression='zip',usecols=['create_time','sum_open_interest'])
                    for path in sorted((ROOT/'metrics'/symbol).glob(f'{symbol}-metrics-{month}-*.zip'))]
            if not pieces:raise ValueError(f'no archived positioning observations for {symbol}/{month}')
            m=pd.concat(pieces,ignore_index=True)
            m['available']=pd.to_datetime(m.create_time,utc=True).astype('int64')+5*MINUTE
            m=m.sort_values('available').drop_duplicates('available',keep='last')
            stamps=m.available.to_numpy(dtype=np.int64)
            logoi=np.log(m.sum_open_interest.where(m.sum_open_interest>0)).to_numpy(dtype=float)
            current=np.searchsorted(stamps,times,side='right')-1
            for n in (5,15,60):
                prior=np.searchsorted(stamps,times-n*MINUTE,side='right')-1
                valid=(current>=0)&(prior>=0)
                ci=np.maximum(current,0);pi=np.maximum(prior,0)
                valid&=(times-stamps[ci]<=10*MINUTE)&(times-n*MINUTE-stamps[pi]<=10*MINUTE)
                out[f'inventory_change_{n}']=np.nan
                out.loc[valid,f'inventory_change_{n}']=10000*(logoi[ci[valid]]-logoi[pi[valid]])
            scale=out.inventory_change_15.rolling(1440,min_periods=240).std().shift(15)
            out['inventory_change_surprise']=out.inventory_change_15/scale.replace(0,np.nan)
            out=out.reindex(columns=EXTRA).replace([np.inf,-np.inf],np.nan)
            self.tables[symbol]=(times,out.to_numpy(dtype=float))
    def at(self,p):
        times,values=self.tables[p.symbol]
        i=np.searchsorted(times,p.observed_time_ns,side='right')-1
        if i<0 or times[i]!=p.observed_time_ns:raise ValueError('explanatory observation clock mismatch')
        side=int(p.side.value)
        return {name:float(value if name in UNSIGNED else side*value) for name,value in zip(EXTRA,values[i])}

def execute(base,request):
    original_tape=base.Tape
    class PositionedTape(original_tape):
        def __init__(self,month):
            super().__init__(month)
            self.inventory=InventoryObservations(month,self.raw)
        def plans(self):
            plans,stats,no_trades=super().plans()
            attached=[replace(p,features={**p.features,**self.inventory.at(p)}) for p in plans]
            return attached,stats,no_trades
    ensure_extra(request['months'],base.OUT)
    base.Tape=PositionedTape
    base.FEATURES=tuple(base.FEATURES)+EXTRA
    import directional_transition_model as model
    base.fit=lambda labels,train_end,cal_end:model.fit(base,labels,train_end,cal_end)
    base.main()
