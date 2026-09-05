"""Investigate a frozen-decision failure through an evolving causal auction."""
from __future__ import annotations
import hashlib,json,pickle,traceback
import numpy as np
import pandas as pd
import research as r
import fast_auction
from episode_states import make_training_rows
from episode_decision import fit_belief,expected_log_gain
from episode_strategy import strategy_type


def run():
    request=json.loads((r.HERE/'request.json').read_text())
    if request.get('driver')!='evolving_auction':
        import path_research
        return path_research.run()
    (r.OUT/'error.txt').unlink(missing_ok=True)
    fast_auction.install()
    assert expected_log_gain(.5,103000.,97000.,100000.)<0
    assert expected_log_gain(.8,103000.,97000.,100000.)>0
    tapes={};seeds_by_month={};labels=[]
    source=b''.join((r.HERE/name).read_bytes() for name in ('episode_states.py','path_state.py','auction_reuse_policy.py','policy.py'))
    key=hashlib.sha256(source).hexdigest()[:16]
    calibration_end=r.ns(request['calibration_end'])
    for month in request['months']:
        tape=r.Tape(month);tapes[month]=tape
        seeds,stats=tape.plans();seeds_by_month[month]=seeds
        if int(next(iter(tape.raw.values())).ts.iloc[0])>=calibration_end:continue
        cache=r.CACHE/f'{month}-{key}-states.pkl'
        if cache.exists():data=pd.read_pickle(cache)
        else:
            data=make_training_rows(tape,seeds)
            data.to_pickle(cache)
        if len(data):data['month']=month;labels.append(data)
    labels=pd.concat(labels,ignore_index=True)
    train=labels[labels.label_closed<r.ns(request['train_end'])].copy()
    calibration=labels[(labels.observed_time_ns>=r.ns(request['train_end']))&
                       (labels.label_closed<calibration_end)].copy()
    belief,fit=fit_belief(train,calibration)
    fit.update(train_end=request['train_end'],calibration_end=request['calibration_end'])
    prefix=request['prefix']
    (r.OUT/f'{prefix}_fit.json').write_text(json.dumps(fit,indent=2))
    (r.OUT/f'{prefix}_belief.pkl').write_bytes(pickle.dumps(belief))
    belief.model.save_model(str(r.OUT/f'{prefix}_model.txt'))
    print('BELIEF_FIT',json.dumps(fit),flush=True)
    observations=[];original=r.AccountStrategy
    modes={'endpoint':(False,False),'wait':(True,False),'feedback':(True,True)}
    for job in request['experiments']:
        if r.ns(job['start'])<calibration_end:raise ValueError('evaluation precedes model frontier')
        tape=tapes[job['month']];seeds=seeds_by_month[job['month']]
        for mode in request.get('variants',['endpoint','wait','feedback']):
            wait,feedback=modes[mode];scores={}
            r.AccountStrategy=strategy_type(tape,seeds,belief,scores,wait,feedback)
            try:
                name=f'{prefix}_{mode}_{job["name"]}'
                result=r.backtest(tape,seeds,scores,name,job['start'],job['end'],stress=job.get('stress',1.))
                result.update(model_training_end=request['train_end'],model_calibration_end=request['calibration_end'])
                observations.append(result)
                (r.OUT/f'{prefix}_results.json').write_text(json.dumps(observations,indent=2))
            finally:r.AccountStrategy=original
    (r.OUT/'latest.json').write_text(json.dumps(observations,indent=2));(r.OUT/'error.txt').unlink(missing_ok=True)

if __name__=='__main__':
    try:run()
    except Exception:
        (r.OUT/'error.txt').write_text(traceback.format_exc());raise
