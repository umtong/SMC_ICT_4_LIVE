"""Small control-wave experiment using the existing Nautilus account runner."""
from __future__ import annotations
import hashlib,json,pickle
from pathlib import Path
import numpy as np
import pandas as pd
import research as r
from astra_policy import Observation, MINUTE, SYMBOLS
from control_wave import ControlWavePolicy, FEATURES
from models import fit_offset

class MarketTape(r.Tape):
    """Reuse the exchange loaders and account, without unused explanatory feeds."""
    def __init__(self, month, symbols=SYMBOLS):
        self.month=month; self.symbols=tuple(symbols)
        self.raw={s:r.load_bars(s,month) for s in self.symbols}
        self.marks={s:r.load_bars(s,month,'markPriceKlines') for s in self.symbols}
        self.funding=[row for s in self.symbols for row in r.load_funding(s,month)]
        self.instruments={s:r.make_instrument_with_fee_profile(s,r.FEE_PROFILES['usd_m_vip0']) for s in self.symbols}
        self.ticks={s:float(i.price_increment) for s,i in self.instruments.items()}
        self.mark_arrays={s:(d.ts.to_numpy(dtype=np.int64), d.close.to_numpy(dtype=float)) for s,d in self.marks.items()}
        times=[d.ts.to_numpy(dtype=np.int64) for d in self.raw.values()]
        if not all(np.array_equal(times[0],v) for v in times):raise ValueError('different universe timestamps')
        if not np.all(np.diff(times[0])==MINUTE):raise ValueError('missing exchange minutes')

    def plans(self):
        source=(r.HERE/'control_wave.py').read_bytes()+(r.HERE/'policy.py').read_bytes()
        key=hashlib.sha256(source).hexdigest()[:16]
        path=r.CACHE/f'control-{self.month}-{key}.pkl'
        if path.exists():return pickle.loads(path.read_bytes())
        arrays={s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count']].to_numpy() for s,d in self.raw.items()}
        policy=ControlWavePolicy(self.ticks)
        plans=[]
        for i in range(len(next(iter(arrays.values())))):
            observations={}
            for s,a in arrays.items():
                t,o,h,l,c,v,b,q,n=a[i]
                observations[s]=Observation(int(t),float(o),float(h),float(l),float(c),float(v),float(b),float(q),int(n))
            plans.extend(policy.observe(observations))
        value=(plans,{s:dict(m.stats) for s,m in policy.markets.items()})
        path.write_bytes(pickle.dumps(value))
        print('CONTROL_PROPOSALS', self.month,len(plans),value[1],flush=True)
        return value

def run():
    request=json.loads((r.HERE/'request.json').read_text())
    tapes={}; plans_by={}; labels=[]; generation={}
    for month in request['months']:
        tape=MarketTape(month);tapes[month]=tape
        plans,stats=tape.plans();plans_by[month]=plans
        labeled=tape.outcomes(plans)
        if len(labeled):
            labeled['month']=month;labels.append(labeled)
        generation[month]={'counts':stats,'labels':r.label_summary(labeled) if len(labeled) else {}}
    prefix=request['prefix']
    (r.OUT/f'{prefix}_generation.json').write_text(json.dumps(generation,indent=2))
    labels=pd.concat(labels,ignore_index=True) if labels else pd.DataFrame()
    if not len(labels):raise RuntimeError('no control-wave proposals resolved; inspect generation counts')
    train=labels[labels.label_closed<r.ns(request['train_end'])].copy()
    cal=labels[(labels.observed_time_ns>=r.ns(request['train_end']))&(labels.label_closed<r.ns(request['calibration_end']))].copy()
    decision,details=fit_offset(train,cal,FEATURES)
    details.update(train_end=request['train_end'],calibration_end=request['calibration_end'])
    (r.OUT/f'{prefix}_fit.json').write_text(json.dumps(details,indent=2))
    (r.OUT/f'{prefix}_decision.pkl').write_bytes(pickle.dumps(decision))
    print('FIT_CONTROL',len(train),len(cal),flush=True)
    output=[]
    for job in request['experiments']:
        if r.ns(job['start'])<r.ns(request['calibration_end']):raise ValueError('evaluation overlaps fitting')
        tape=tapes[job['month']];plans=plans_by[job['month']]
        for variant in request.get('variants',['raw','learned']):
            if variant=='raw':scores={p.plan_id:(1.-p.features['cost_r'],.5) for p in plans}
            elif variant=='learned':scores=decision.scores(plans)
            else:raise ValueError(variant)
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
