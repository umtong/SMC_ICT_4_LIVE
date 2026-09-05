"""Excursion measurement and second-auction entry experiment."""
from __future__ import annotations
import hashlib,json,pickle
import numpy as np
import pandas as pd
import research as r
from washout_research import WashoutTape
from astra_policy import Observation
from extended_inputs import EXTRA_FEATURES
from excursion_policy import ExcursionPolicy,FEATURES
from models import fit_offset

class ExcursionTape(WashoutTape):
    def proposals(self,entry):
        if entry not in ('direct','test'):raise ValueError(entry)
        source=b''.join((r.HERE/p).read_bytes() for p in ('excursion_policy.py','washout_policy.py','policy.py','extended_inputs.py'))
        key=hashlib.sha256(source+entry.encode()).hexdigest()[:16]
        path=r.CACHE/f'excursion-{self.month}-{key}.pkl'
        if path.exists():return pickle.loads(path.read_bytes())
        arrays={s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count']].to_numpy() for s,d in self.raw.items()}
        for s,d in self.raw.items():
            if not np.array_equal(d.ts.to_numpy(dtype=np.int64),self.extra.tables[s][0]):raise ValueError('explanatory clock mismatch')
        policy=ExcursionPolicy(self.ticks,require_test=(entry=='test'));plans=[]
        for i in range(len(next(iter(arrays.values())))):
            bars={};extra={}
            for s,a in arrays.items():
                t,o,h,l,c,v,b,q,n=a[i]
                bars[s]=Observation(int(t),float(o),float(h),float(l),float(c),float(v),float(b),float(q),int(n))
                extra[s]=dict(zip(EXTRA_FEATURES,self.extra.tables[s][1][i],strict=True))
            plans.extend(policy.observe(bars,extra))
        result=(plans,{s:dict(m.stats) for s,m in policy.markets.items()})
        path.write_bytes(pickle.dumps(result))
        print('EXCURSION_PROPOSALS',self.month,entry,len(plans),result[1],flush=True)
        return result

def run():
    request=json.loads((r.HERE/'request.json').read_text());prefix=request['prefix']
    tapes={m:ExcursionTape(m) for m in request['months']}
    results=[];generation={}
    for entry in request.get('entries',['direct','test']):
        plans_by={};labels=[];generation[entry]={}
        for month,tape in tapes.items():
            plans,stats=tape.proposals(entry);plans_by[month]=plans
            data=tape.outcomes(plans)
            if len(data):data['month']=month;labels.append(data)
            generation[entry][month]={'counts':stats,'labels':r.label_summary(data) if len(data) else {}}
        labels=pd.concat(labels,ignore_index=True) if labels else pd.DataFrame()
        if not len(labels):raise RuntimeError(f'no resolved {entry} proposals')
        train=labels[labels.label_closed<r.ns(request['train_end'])].copy()
        cal=labels[(labels.observed_time_ns>=r.ns(request['train_end']))&(labels.label_closed<r.ns(request['calibration_end']))].copy()
        decision,details=fit_offset(train,cal,FEATURES)
        details.update(train_end=request['train_end'],calibration_end=request['calibration_end'])
        (r.OUT/f'{prefix}_{entry}_fit.json').write_text(json.dumps(details,indent=2))
        (r.OUT/f'{prefix}_{entry}_decision.pkl').write_bytes(pickle.dumps(decision))
        for job in request['experiments']:
            if r.ns(job['start'])<r.ns(request['calibration_end']):raise ValueError('evaluation overlaps fit')
            plans=plans_by[job['month']];tape=tapes[job['month']]
            for selection in request.get('variants',['price','learned']):
                if selection=='learned':scores=decision.scores(plans)
                elif selection=='price':scores={p.plan_id:(1-p.features['cost_r'],.5) for p in plans}
                else:raise ValueError(selection)
                name=f'{prefix}_{entry}_{selection}_{job["name"]}'
                result=r.backtest(tape,plans,scores,name,job['start'],job['end'],stress=job.get('stress',1.))
                result.update(model_training_end=request['train_end'],model_calibration_end=request['calibration_end'])
                results.append(result)
                subset=labels[(labels.month==job['month'])&(labels.observed_time_ns>=r.ns(job['start']))&(labels.observed_time_ns<r.ns(job['end']))].copy()
                if len(subset):
                    subset['forecast']=[scores.get(p,(-1.,0.))[1] for p in subset.plan_id]
                    subset['utility']=[scores.get(p,(-1.,0.))[0] for p in subset.plan_id]
                    subset.to_csv(r.OUT/name/'candidate_outcomes.csv',index=False)
    (r.OUT/f'{prefix}_generation.json').write_text(json.dumps(generation,indent=2))
    (r.OUT/f'{prefix}_results.json').write_text(json.dumps(results,indent=2))
    (r.OUT/'latest.json').write_text(json.dumps(results,indent=2))
    (r.OUT/'error.txt').unlink(missing_ok=True)
