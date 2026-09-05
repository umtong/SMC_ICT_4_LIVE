"""Test an omitted source decision: full liquidation when the premise changes."""
from __future__ import annotations
import json,pickle
import research as r
import fast_auction
from source_exit import strategy_type


def run():
    request=json.loads((r.HERE/'request.json').read_text());fast_auction.install()
    (r.OUT/'error.txt').unlink(missing_ok=True)
    original=r.AccountStrategy;results=[]
    for job in request['experiments']:
        tape=r.Tape(job['month']);plans,_=tape.plans()
        # A raw comparison isolates the decision rather than refitting a model
        # that was trained for a different terminal payoff.
        scores={p.plan_id:(1-p.features['cost_r'],.5) for p in plans}
        for mode in ('boundaries','source_exit'):
            r.AccountStrategy=original if mode=='boundaries' else strategy_type(tape)
            try:
                result=r.backtest(tape,plans,scores,f'{request["prefix"]}_{mode}_{job["name"]}',job['start'],job['end'])
                results.append(result)
            finally:r.AccountStrategy=original
    (r.OUT/f'{request["prefix"]}_results.json').write_text(json.dumps(results,indent=2))
    (r.OUT/'latest.json').write_text(json.dumps(results,indent=2))
