"""Short experiments over causal auction observations; one Nautilus account."""
from __future__ import annotations
import hashlib,json,pickle,traceback
import numpy as np
import pandas as pd
import research as r
from models import fit_offset
from path_state import PathTable,FEATURES,test_order_information
import fast_auction


def run():
    request=json.loads((r.HERE/'request.json').read_text());driver=request.get('driver')
    if driver=='cash_dislocation':
        import basis_research
        return basis_research.run()
    if driver=='evolving_auction':
        import episode_research
        return episode_research.run()
    if request.get('fastbook_check',False):fast_auction.check_equivalence(r.Tape(request['months'][0]))
    fast_auction.install()
    if driver not in ('ordered_path','structure_context'):return r.main()
    columns_new=FEATURES
    if driver=='structure_context':
        import structure_context as context
        columns_new=context.FEATURES
    else:test_order_information()
    (r.OUT/'error.txt').unlink(missing_ok=True)
    tapes={};plans_by_month={};labels=[]
    for month in request['months']:
        tape=r.Tape(month);tapes[month]=tape
        original,stats=tape.plans()
        if driver=='structure_context':
            digest=hashlib.sha256((r.HERE/'structure_context.py').read_bytes()+(r.HERE/'policy.py').read_bytes()+(r.HERE/'auction_reuse_policy.py').read_bytes()).hexdigest()[:16]
            cache=r.CACHE/f'{month}-{digest}-structure.pkl'
            if cache.exists():plans=pickle.loads(cache.read_bytes())
            else:
                plans=context.attach_context(tape,original);cache.write_bytes(pickle.dumps(plans))
        else:plans=PathTable(tape.raw).attach(original)
        plans_by_month[month]=plans
        label=tape.outcomes(plans)
        if len(label):label['month']=month;labels.append(label)
        print('CAUSAL_OBSERVATIONS',driver,month,len(plans),flush=True)
    labels=pd.concat(labels,ignore_index=True)
    train=labels[labels.label_closed<r.ns(request['train_end'])].copy()
    calibration=labels[(labels.observed_time_ns>=r.ns(request['train_end']))&
                       (labels.label_closed<r.ns(request['calibration_end']))].copy()
    decisions={}
    for variant in request.get('variants',['ordered']):
        columns=tuple(request['features']) if variant=='base' else columns_new
        decision,fit=fit_offset(train,calibration,columns)
        decisions[variant]=decision
        fit.update(train_end=request['train_end'],calibration_end=request['calibration_end'])
        (r.OUT/f'{request["prefix"]}_{variant}_fit.json').write_text(json.dumps(fit,indent=2))
        (r.OUT/f'{request["prefix"]}_{variant}_decision.pkl').write_bytes(pickle.dumps(decision))
        print('FIT',variant,len(train),len(calibration),flush=True)
    observations=[]
    for job in request['experiments']:
        if r.ns(job['start'])<r.ns(request['calibration_end']):raise ValueError('evaluation overlaps fitted label frontier')
        month=job['month'];plans=plans_by_month[month]
        for variant,decision in decisions.items():
            name=f'{request["prefix"]}_{variant}_{job["name"]}'
            scores=decision.scores(plans)
            summary=r.backtest(tapes[month],plans,scores,name,job['start'],job['end'],stress=job.get('stress',1.))
            summary['model_training_end']=request['train_end'];summary['model_calibration_end']=request['calibration_end']
            observations.append(summary)
            subset=labels[(labels.month==month)&(labels.observed_time_ns>=r.ns(job['start']))&
                          (labels.observed_time_ns<r.ns(job['end']))].copy()
            if len(subset):
                subset['forecast']=[scores.get(i,(-1,0))[1] for i in subset.plan_id]
                subset['utility']=[scores.get(i,(-1,0))[0] for i in subset.plan_id]
                subset.to_csv(r.OUT/name/'candidate_outcomes.csv',index=False)
    (r.OUT/f'{request["prefix"]}_results.json').write_text(json.dumps(observations,indent=2))
    (r.OUT/'latest.json').write_text(json.dumps(observations,indent=2))
    (r.OUT/'error.txt').unlink(missing_ok=True)

if __name__=='__main__':
    try:run()
    except Exception:
        (r.OUT/'error.txt').write_text(traceback.format_exc());raise
