"""Diagnose objective semantics, not fit a desired reward/risk number."""
from __future__ import annotations
import hashlib,json,pickle,traceback
import pandas as pd
import research as r
import fast_auction
import structure_context as context
from role_frontier import apply_frontiers
from models import fit_offset


def run():
    request=json.loads((r.HERE/'request.json').read_text());fast_auction.install()
    (r.OUT/'error.txt').unlink(missing_ok=True)
    prefix=request['prefix'];tapes={};proposals={};labels=[];changes={}
    original_digest=hashlib.sha256((r.HERE/'structure_context.py').read_bytes()+(r.HERE/'policy.py').read_bytes()+(r.HERE/'auction_reuse_policy.py').read_bytes()).hexdigest()[:16]
    digest=hashlib.sha256((r.HERE/'role_frontier.py').read_bytes()+original_digest.encode()).hexdigest()[:16]
    for month in request['months']:
        tape=r.Tape(month);tapes[month]=tape
        cache=r.CACHE/f'{month}-{digest}-frontier.pkl'
        if cache.exists():plans,stats=pickle.loads(cache.read_bytes())
        else:
            prior_cache=r.CACHE/f'{month}-{original_digest}-structure.pkl'
            if prior_cache.exists():original=pickle.loads(prior_cache.read_bytes())
            else:
                original,_=tape.plans();original=context.attach_context(tape,original)
                prior_cache.write_bytes(pickle.dumps(original))
            plans,stats=apply_frontiers(tape,original)
            cache.write_bytes(pickle.dumps((plans,stats)))
        changes[month]=stats;proposals[month]=plans
        data=tape.outcomes(plans)
        if len(data):labels.append(data)
        print('ROLE_FRONTIERS',month,len(plans),json.dumps(stats),flush=True)
    labels=pd.concat(labels,ignore_index=True)
    train=labels[labels.label_closed<r.ns(request['train_end'])]
    calibration=labels[(labels.observed_time_ns>=r.ns(request['train_end']))&(labels.label_closed<r.ns(request['calibration_end']))]
    decision,fit=fit_offset(train,calibration,context.FEATURES)
    fit.update(train_end=request['train_end'],calibration_end=request['calibration_end'])
    (r.OUT/f'{prefix}_fit.json').write_text(json.dumps(fit,indent=2))
    (r.OUT/f'{prefix}_changes.json').write_text(json.dumps(changes,indent=2))
    (r.OUT/f'{prefix}_decision.pkl').write_bytes(pickle.dumps(decision))
    results=[]
    for job in request['experiments']:
        if r.ns(job['start'])<r.ns(request['calibration_end']):raise ValueError('overlapping development frontier')
        tape=tapes[job['month']];plans=proposals[job['month']]
        for variant in request.get('variants',['context']):
            scores=decision.scores(plans) if variant=='context' else {p.plan_id:(1-p.features['cost_r'],.5) for p in plans}
            result=r.backtest(tape,plans,scores,f'{prefix}_{variant}_{job["name"]}',job['start'],job['end'])
            result.update(model_training_end=request['train_end'],model_calibration_end=request['calibration_end'])
            results.append(result)
    (r.OUT/f'{prefix}_results.json').write_text(json.dumps(results,indent=2))
    (r.OUT/'latest.json').write_text(json.dumps(results,indent=2))

if __name__=='__main__':
    try:run()
    except Exception:
        (r.OUT/'error.txt').write_text(traceback.format_exc());raise
