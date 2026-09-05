"""Direct economic observations -> causal plans -> existing account engine."""
from __future__ import annotations
import hashlib,json,pickle
import numpy as np
import pandas as pd
import research as r
from control_research import MarketTape
from astra_policy import Observation,MINUTE
from forced_flow_inputs import CACHE as OBSERVED_CACHE
from forced_response import ForcedResponsePolicy

COLUMNS=('buy_5','sell_5','buy_threshold','sell_threshold','bid','ask','ready')

class ForcedTape(MarketTape):
    def __init__(self,month,start,end):
        super().__init__(month)
        self.input_start=start;self.input_end=end
        first=r.ns(start)-3*1440*MINUTE;last=r.ns(end)
        self.raw={s:d[(d.ts>first)&(d.ts<=last)].reset_index(drop=True) for s,d in self.raw.items()}
        self.evidence={}
        for symbol,frame in self.raw.items():
            path=OBSERVED_CACHE/f'{symbol}-{start}-{end}-observed.parquet'
            depth_path=OBSERVED_CACHE/f'{symbol}-{start}-{end}-depth.parquet'
            if not path.exists() or not depth_path.exists():raise FileNotFoundError(f'prepare direct forcing inputs first: {symbol} {start} {end}')
            received=pd.read_parquet(path);depth=pd.read_parquet(depth_path)
            index=pd.Index(frame.ts.to_numpy(dtype=np.int64),name='ts')
            out=pd.DataFrame(index=index)
            published=((received.received_time.to_numpy(dtype=np.int64)//MINUTE)+1)*MINUTE
            received=received.assign(ts=published)
            covered=(index>r.ns(start))&(index<=r.ns(end))
            for side,name in ((1,'buy'),(-1,'sell')):
                sums=received[received.side==side].groupby('ts').reported_notional.sum()
                # Zero is zero archived reports in a requested hour, never a
                # claim that the exchange's complete liquidation volume was zero.
                one=sums.reindex(index,fill_value=0.).astype(float)
                one.loc[~covered]=np.nan
                five=one.rolling(5,min_periods=5).sum()
                out[f'{name}_5']=five
                out[f'{name}_threshold']=five.shift(1).rolling(1440,min_periods=240).quantile(.98)
            stamps=depth.ts.to_numpy(dtype=np.int64)
            available=np.searchsorted(stamps,index,side='right')-1
            pos=np.maximum(available,0)
            valid=(available>=0)&(index.to_numpy()-stamps[pos]<=90_000_000_000)
            for source,target in (('bid_notional','bid'),('ask_notional','ask')):
                value=depth[source].to_numpy(dtype=float)[pos]
                out[target]=np.where(valid,value,np.nan)
            out['ready']=covered&out[['buy_threshold','sell_threshold']].notna().all(axis=1)
            self.evidence[symbol]=out[list(COLUMNS)].to_numpy(dtype=float)

    def proposals(self,selection):
        source=(r.HERE/'forced_response.py').read_bytes()+(r.HERE/'forced_response_research.py').read_bytes()
        key=hashlib.sha256(source+selection.encode()).hexdigest()[:16]
        path=r.CACHE/f'forced-{self.input_start}-{self.input_end}-{key}.pkl'
        if path.exists():return pickle.loads(path.read_bytes())
        arrays={s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count']].to_numpy() for s,d in self.raw.items()}
        policy=ForcedResponsePolicy(self.ticks,selection)
        plans=[]
        for i in range(len(next(iter(arrays.values())))):
            bars={};evidence={}
            for s,a in arrays.items():
                t,o,h,l,c,v,b,q,n=a[i]
                bars[s]=Observation(int(t),float(o),float(h),float(l),float(c),float(v),float(b),float(q),int(n))
                evidence[s]=dict(zip(COLUMNS,self.evidence[s][i],strict=True))
                evidence[s]['ready']=bool(evidence[s]['ready'])
            plans.extend(policy.observe(bars,evidence))
        result=(plans,{s:dict(m.stats) for s,m in policy.markets.items()})
        path.write_bytes(pickle.dumps(result))
        print('FORCED_RESPONSE_PROPOSALS',self.input_start,selection,len(plans),result[1],flush=True)
        return result


def run():
    request=json.loads((r.HERE/'request.json').read_text());prefix=request['prefix']
    results=[];generation={}
    for job in request['experiments']:
        tape=ForcedTape(job['month'],job['input_start'],job['end'])
        for selection in request.get('variants',['response','resupply']):
            plans,stats=tape.proposals(selection)
            scores={}
            for p in plans:
                side=int(p.side.value);entry=p.entry*(1+side*.0001)
                net_reward=side*(p.target-entry)-.0005*entry-.0002*p.target
                strength=p.features['forcing_strength']
                score=strength/(1+p.features['cost_r']) if net_reward>0 else -1.
                scores[p.plan_id]=(score,float('nan'))
            name=f'{prefix}_{selection}_{job["name"]}'
            results.append(r.backtest(tape,plans,scores,name,job['start'],job['end'],stress=job.get('stress',1.)))
            labels=tape.outcomes(plans)
            generation[name]={'counts':stats,'labels':r.label_summary(labels) if len(labels) else {}}
            if len(labels):labels.to_csv(r.OUT/name/'candidate_outcomes.csv',index=False)
    (r.OUT/f'{prefix}_generation.json').write_text(json.dumps(generation,indent=2))
    (r.OUT/f'{prefix}_results.json').write_text(json.dumps(results,indent=2))
    (r.OUT/'latest.json').write_text(json.dumps(results,indent=2))
    (r.OUT/'error.txt').unlink(missing_ok=True)
