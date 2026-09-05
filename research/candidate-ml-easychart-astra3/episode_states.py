"""A completed liquidity event is a changing decision, not a frozen candle label.

A state is not another trade. All states retain the same causal event identity,
initial structural invalidation and destination. Entry is never allowed after
one of those boundaries has traded, nor at a worse price than the seed proposal.
Five-minute reassessment is a research observation cadence, not a holding limit.

Training endpoint labels are taken from future data ONLY in make_training_rows.
The online state constructor below has no access to outcomes or future prices.
"""
from __future__ import annotations
from dataclasses import replace
import math
import numpy as np
import pandas as pd
from astra_policy import MINUTE
from path_state import PathTable,FEATURES

REASSESS_MINUTES=5


def current_plan(seed,now:int,price:float,tick:float,paths:PathTable,extra):
    side=int(seed.side.value)
    risk=side*(price-seed.stop);reward=side*(seed.target-price)
    if risk<=tick*.5 or reward<=tick*.5:return None
    f=dict(seed.features)
    # Reconstruct the original source range unit. It is fixed at the event,
    # while the remaining entry/stop geometry changes with observed price.
    unit=seed.entry*seed.features['risk_bps']/10000./max(seed.features['risk_range'],1e-12)
    f.update(risk_bps=10000.*risk/price,risk_range=risk/max(unit,tick),
             cost_r=.0006*(price+seed.stop)/risk,planned_rr=reward/risk,
             entry_distance=side*(price-seed.source_level)/max(unit,tick))
    f.update(paths.at(seed,now))
    f.update(extra.at(seed.symbol,now,side,10000.*unit/price))
    return replace(seed,plan_id=f'{seed.plan_id}:STATE:{now}',observed_time_ns=now,
                   entry=price,gross_rr=reward/risk,features=f)


def eligible_entry(seed,current)->bool:
    # Waiting is for a better-informed retest, not permission to chase a move.
    return current.gross_rr>=1. and int(seed.side.value)*(current.entry-seed.entry)<=0.


class EvolvingAuctions:
    """Point-in-time feature source usable by a replay or streaming strategy.

    PathTable supplies prefixes only. Its .at/describe formulas are shared with
    offline construction. No endpoint or label table enters this object.
    """
    def __init__(self,tape,seeds):
        self.tape=tape;self.paths=PathTable(tape.raw)
        self.seeds={p.plan_id:p for p in seeds};self.active={};self.by_time={}
        self.latest={};self.terminated=set();self.last_ts=0
        for p in seeds:self.by_time.setdefault(p.observed_time_ns,[]).append(p)
    def observe(self,bars):
        now=next(iter(bars.values())).ts
        if now<=self.last_ts:raise ValueError('non-increasing episode clock')
        self.last_ts=now
        for key,seed in list(self.active.items()):
            b=bars[seed.symbol];side=int(seed.side.value)
            stop_hit=b.low<=seed.stop if side>0 else b.high>=seed.stop
            target_hit=b.high>=seed.target if side>0 else b.low<=seed.target
            if stop_hit or target_hit:
                self.terminated.add(key);self.active.pop(key);self.latest.pop(key,None)
        for seed in self.by_time.get(now,[]):self.active[seed.plan_id]=seed
        entries=[]
        for key,seed in self.active.items():
            if (now-seed.observed_time_ns)%(REASSESS_MINUTES*MINUTE):continue
            b=bars[seed.symbol]
            state=current_plan(seed,now,b.close,self.tape.ticks[seed.symbol],self.paths,self.tape.extra)
            if state is None:continue
            self.latest[key]=state
            if eligible_entry(seed,state):entries.append(state)
        return entries
    def root(self,plan):return plan.plan_id.rsplit(':STATE:',1)[0]
    def state_for_position(self,plan,now):
        state=self.latest.get(self.root(plan))
        return state if state is not None and state.observed_time_ns==now else None


def make_training_rows(tape,seeds):
    """Endpoint-only labels: no hindsight choice of entry time or exit price."""
    paths=PathTable(tape.raw);endpoints=tape.outcomes(seeds)
    if not len(endpoints):return pd.DataFrame()
    result=[]
    by_id={p.plan_id:p for p in seeds}
    arrays={s:d[['ts','high','low','close']].to_numpy() for s,d in tape.raw.items()}
    for endpoint in endpoints.itertuples(index=False):
        seed=by_id[endpoint.plan_id];a=arrays[seed.symbol];side=int(seed.side.value)
        j=int(np.searchsorted(a[:,0],seed.observed_time_ns,side='left'))
        k=int(np.searchsorted(a[:,0],endpoint.label_closed,side='left'))
        if j>=len(a) or int(a[j,0])!=seed.observed_time_ns:raise ValueError('seed clock absent')
        for index in range(j,k):
            now,high,low,price=a[index];now=int(now)
            # A mere target touch also ends further entry observation: that
            # destination is no longer unspent even if a resting limit needs
            # trade-through. Existing positions keep their exchange protection.
            if index>j and ((low<=seed.stop or high>=seed.target) if side>0 else (high>=seed.stop or low<=seed.target)):
                break
            if (now-seed.observed_time_ns)%(REASSESS_MINUTES*MINUTE):continue
            state=current_plan(seed,now,float(price),tape.ticks[seed.symbol],paths,tape.extra)
            if state is None:continue
            row=state.record()
            row.update(label_closed=int(endpoint.label_closed),label_target=int(endpoint.label_target),
                       seed_id=seed.plan_id,entry_eligible=eligible_entry(seed,state),
                       label_ambiguous=bool(endpoint.label_ambiguous))
            result.append(row)
    frame=pd.DataFrame(result)
    print('EVOLVING_STATES',tape.month,len(seeds),len(frame),flush=True)
    return frame
