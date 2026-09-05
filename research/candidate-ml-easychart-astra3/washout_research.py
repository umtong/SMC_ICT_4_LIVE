"""Short experiment for the public-pool washout hypothesis, not a new simulator."""
from __future__ import annotations
import hashlib,json,pickle
import numpy as np
import pandas as pd
import research as r
from control_research import MarketTape
from astra_policy import Observation,SYMBOLS
from extended_inputs import ExtraObservations,EXTRA_FEATURES
from washout_policy import WashoutPolicy,FEATURES
from models import fit_offset

class WashoutTape(MarketTape):
    def __init__(self,month,symbols=SYMBOLS):
        super().__init__(month,symbols)
        self.extra=ExtraObservations(month,self.raw)
    def plans(self):
        source=b''.join((r.HERE/p).read_bytes() for p in ('washout_policy.py','policy.py','extended_inputs.py'))
        key=hashlib.sha256(source).hexdigest()[:16]
        path=r.CACHE/f'washout-{self.month}-{key}.pkl'
        if path.exists():return pickle.loads(path.read_bytes())
        arrays={s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count']].to_numpy() for s,d in self.raw.items()}
        for s,d in self.raw.items():
            if not np.array_equal(d.ts.to_numpy(dtype=np.int64),self.extra.tables[s][0]):raise ValueError('explanatory clock mismatch')
        policy=WashoutPolicy(self.ticks);plans=[]
        for i in range(len(next(iter(arrays.values())))):
            bars={};extra={}
            for s,a in arrays.items():
                t,o,h,l,c,v,b,q,n=a[i]
                bars[s]=Observation(int(t),float(o),float(h),float(l),float(c),float(v),float(b),float(q),int(n))
                extra[s]=dict(zip(EXTRA_FEATURES,self.extra.tables[s][1][i],strict=True))
            plans.extend(policy.observe(bars,extra))
        result=(plans,{s:dict(m.stats) for s,m in policy.markets.items()})
        path.write_bytes(pickle.dumps(result))
        print('WASHOUT_PROPOSALS',self.month,len(plans),result[1],flush=True)
        return result

def run():
    request=json.loads((r.HERE/'request.json').read_text())
    prefix=request['prefix'];tapes={};plans_by={};labels=[];generation={}
    for month in request['months']:
        tape=WashoutTape(month);tapes[month]=tape
        plans,stats=tape.plans();plans_by[month]=plans
        data=tape.outcomes(plans)
        if len(data):data['month']=month;labels.append(data)
        generation[month]={'counts':stats,'labels':r.label_summary(data) if len(data) else {}}
    (r.OUT/f'{prefix}_generation.json').write_text(json.dumps(generation,indent=2))
    labels=pd.concat(labels,ignore_index=True) if labels else pd.DataFrame()
    if not len(labels):raise RuntimeError('washout geometry generated no resolved proposals')
    train=labels[labels.label_closed<r.ns(request['train_end'])].copy()
    cal=labels[(labels.observed_time_ns>=r.ns(request['train_end']))&(labels.label_closed<r.ns(request['calibration_end']))].copy()
    decision,details=fit_offset(train,cal,FEATURES)
    details.update(train_end=request['train_end'],calibration_end=request['calibration_end'])
    (r.OUT/f'{prefix}_fit.json').write_text(json.dumps(details,indent=2))
    (r.OUT/f'{prefix}_decision.pkl').write_bytes(pickle.dumps(decision))
    output=[]
    for job in request['experiments']:
        if r.ns(job['start'])<r.ns(request['calibration_end']):raise ValueError('evaluation overlaps fit')
        plans=plans_by[job['month']];tape=tapes[job['month']]
        for variant in request.get('variants',['price','derivatives','learned']):
            if variant=='learned':scores=decision.scores(plans)
            else:
                scores={}
                for p in plans:
                    f=p.features
                    supported=f['oi_change']<0 and f['basis_stress']>0 and f['basis_repair']>0
                    if variant not in ('price','derivatives'):raise ValueError(variant)
                    scores[p.plan_id]=(1.-f['cost_r'] if variant=='price' or supported else -1.,.5)
            name=f'{prefix}_{variant}_{job["name"]}'
            result=r.backtest(tape,plans,scores,name,job['start'],job['end'],stress=job.get('stress',1.))
            result.update(model_training_end=request['train_end'],model_calibration_end=request['calibration_end'])
            output.append(result)
            subset=labels[(labels.month==job['month'])&(labels.observed_time_ns>=r.ns(job['start']))&(labels.observed_time_ns<r.ns(job['end']))].copy()
            if len(subset):
                subset['forecast']=[scores.get(p,(-1.,0.))[1] for p in subset.plan_id]
                subset['utility']=[scores.get(p,(-1.,0.))[0] for p in subset.plan_id]
                subset.to_csv(r.OUT/name/'candidate_outcomes.csv',index=False)
    (r.OUT/f'{prefix}_results.json').write_text(json.dumps(output,indent=2))
    (r.OUT/'latest.json').write_text(json.dumps(output,indent=2))
    (r.OUT/'error.txt').unlink(missing_ok=True)
