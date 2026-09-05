"""Nautilus-only raw experiment for the first-return passive-entry hypothesis."""
import numpy as np
import pandas as pd
from passive_execution import PassiveAccountStrategy

def execute(base,request):
    if any(job.get('learned',True) for job in request['experiments']):
        raise ValueError('passive decision labels must include fill and cancellation before fitting')
    base.AccountStrategy=PassiveAccountStrategy
    base.prepare(request['months'])
    tapes={};plans={};summary={}
    for month in request['months']:
        tape=base.Tape(month);opportunities,stats,skips=tape.plans()
        tapes[month]=tape;plans[month]=opportunities
        summary[month]={'plans':len(opportunities),'stats':stats,
                        'mean_planned_rr':float(np.mean([p.gross_rr for p in opportunities])) if opportunities else None,
                        'mean_risk_bps':float(np.mean([p.features['risk_bps'] for p in opportunities])) if opportunities else None}
        base.write(base.OUT/f'{month}_notrade_examples.json',skips[::max(1,len(skips)//20)][:20])
    base.write(base.OUT/'opportunities.json',summary)
    results=[]
    for job in request['experiments']:
        cfg=dict(job);month=cfg.pop('month');cfg.pop('learned',None)
        results.append(base.backtest(tapes[month],plans[month],None,**cfg))
        base.write(base.OUT/'latest.json',results)
    (base.OUT/'error.txt').unlink(missing_ok=True)
