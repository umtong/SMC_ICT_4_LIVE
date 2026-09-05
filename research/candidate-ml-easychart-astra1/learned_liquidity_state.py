"""Shared auction-state proposals for a learned directional value policy.

Unlike the earlier meta-filters, no reversal/continuation direction is assumed.
At a closed five-minute observation the policy describes both directions using
only already-confirmed defenses and pre-existing opposing liquidity. A learned
transition model chooses long, short, or no trade BEFORE committing account risk.
The nearest opposing level is never moved farther away to satisfy reward/risk.

The model sees dimensionless price/participation trajectories, not a symbol ID,
price level, calendar identity or outcome. One pivot-to-opposing-liquidity path
has one causal identity even when it is observed at several successive closes.
The reused account permits at most one execution of that identity.
"""
from collections import Counter, deque
from dataclasses import replace
import math
import numpy as np
from astra_policy import Observation, Plan, WickMap, MINUTE
from domain import Side
from auction_control_survival import aggregate

HORIZONS=(5,15,60,240)
BASE=('risk_bps','risk_range','cost_r','planned_rr','defense_age','target_scale',
      'defense_prominence','target_prominence','defense_distance','target_distance')
FEATURES=BASE+tuple(f'{k}_{n}' for n in HORIZONS for k in
    ('move','flow','efficiency','activity','location','value_distance','market_move','relative_move'))+tuple(
    f'{k}_block_{j}' for j in range(8) for k in ('move','flow','range','location'))+tuple(
    f'{k}_{n}' for n in (15,60,240) for k in ('high_change','low_change'))


class DirectionMarket:
    def __init__(self,symbol,tick):
        self.symbol=symbol;self.tick=tick;self.history=[]
        self.books={tf:WickMap(symbol,tf,tick,pivot_spans=(2,)) for tf in (5,15,60,240)}
        self.aggregates={tf:[] for tf in self.books};self.touched=set()
        self.stats=Counter();self.explanations=[];self.five=[]
        self.snapshot={};self.last_array=None;self.last_unit=0.

    def observe(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('non-contiguous observed minute')
        self.history.append(b)
        for tf in sorted(self.books,reverse=True):
            self.aggregates[tf].append(b)
            if b.ts//MINUTE%tf==0:
                items=self.aggregates[tf];self.aggregates[tf]=[]
                if len(items)==tf:
                    complete=aggregate(items);self.books[tf].observe(complete)
                    if tf==5:self.five.append(complete)
        for book in self.books.values():
            for p in book.pivots[-24:]:
                if p.observed_time_ns<b.ts and p.pivot_id not in self.touched:
                    if (b.high>p.price if p.side=='HIGH' else b.low<p.price):self.touched.add(p.pivot_id)
        if b.ts//MINUTE%5 or len(self.history)<1440 or len(self.five)<16:return []
        a=np.array([(q.open,q.high,q.low,q.close,q.volume,q.delta,q.quote) for q in self.history[-300:]],dtype=float)
        unit=max(float(np.mean(a[-60:,1]-a[-60:,2])),2*self.tick)
        vbase=max(float(np.mean(a[-120:-60,4])),1e-12)
        self.last_array=a;self.last_unit=unit;self.snapshot={}
        for n in HORIZONS:
            q=a[-n:];r=max(float(q[:,1].max()-q[:,2].min()),self.tick)
            vol=max(float(q[:,4].sum()),1e-12)
            vwap=float(q[:,6].sum()/vol) if q[:,6].sum()>0 else float(np.average(q[:,3],weights=np.maximum(q[:,4],1e-12)))
            self.snapshot[n]={'move':(b.close-q[0,0])/(unit*math.sqrt(n)),
                'flow':float(q[:,5].sum()/vol),'efficiency':(b.close-q[0,0])/max(float((q[:,1]-q[:,2]).sum()),self.tick),
                'activity':vol/(n*vbase),'location':(b.close-float(q[:,2].min()))/r,
                'value_distance':(b.close-vwap)/(unit*math.sqrt(n))}
        return [p for s in (1,-1) if (p:=self.proposal(b,s)) is not None]

    def proposal(self,b,s):
        # Recent confirmed five-minute defense, not the smallest stop available.
        side='LOW' if s>0 else 'HIGH'
        defenses=[p for p in self.books[5].pivots[-24:] if p.side==side
                  and p.observed_time_ns<b.ts and p.pivot_id not in self.touched
                  and s*(b.close-p.price)>self.tick]
        if not defenses:self.stats['no_live_defense']+=1;return None
        defense=max(defenses,key=lambda p:p.event_time_ns)
        objectives=[]
        for tf in (15,60,240):
            for p in self.books[tf].pivots[-24:]:
                if p.observed_time_ns<b.ts and p.side!=side and p.pivot_id not in self.touched and s*(p.price-b.close)>self.tick:
                    objectives.append((p,tf))
        if not objectives:self.stats['no_opposing_liquidity']+=1;return None
        objective,tf=min(objectives,key=lambda item:s*(item[0].price-b.close))
        stop=defense.price-s*self.tick;target=objective.price-s*self.tick;entry=b.close
        risk=s*(entry-stop);reward=s*(target-entry);rr=reward/risk
        if rr<1:self.stats['nearest_objective_inadequate']+=1;return None
        unit=self.last_unit
        f=dict(risk_bps=risk/entry*10000,risk_range=risk/(unit*math.sqrt(15)),
            cost_r=.0012*entry/risk,planned_rr=rr,defense_age=(b.ts-defense.event_time_ns)/(15*MINUTE),
            target_scale=math.log(tf/5),defense_prominence=defense.strength_ratio,
            target_prominence=objective.strength_ratio,defense_distance=risk/unit,target_distance=reward/unit)
        for n,values in self.snapshot.items():
            for k,x in values.items():
                f[f'{k}_{n}']=1-x if k=='location' and s<0 else (x if k in ('location','activity') else s*x)
        for j in range(8):
            q=self.five[-1-j];r=max(q.high-q.low,self.tick)
            f[f'move_block_{j}']=s*(q.close-q.open)/(unit*math.sqrt(5))
            f[f'flow_block_{j}']=s*q.delta/max(q.volume,1e-12)
            f[f'range_block_{j}']=r/(unit*math.sqrt(5))
            f[f'location_block_{j}']=(q.close-q.low)/r if s>0 else (q.high-q.close)/r
        for n in (15,60,240):
            for kind in ('HIGH','LOW'):
                q=[p for p in self.books[n].pivots[-24:] if p.side==kind]
                k=(kind.lower() if s>0 else ('low' if kind=='HIGH' else 'high'))+f'_change_{n}'
                f[k]=s*(q[-1].price-q[-2].price)/(unit*math.sqrt(n)) if len(q)>1 else float('nan')
        key=f'{self.symbol}:PATH:{defense.pivot_id}:{objective.pivot_id}'
        self.stats['proposals']+=1
        return Plan(key+f':{b.ts}',key,self.symbol,Side.LONG if s>0 else Side.SHORT,
                    b.ts,defense.event_time_ns,entry,stop,target,rr,defense.price,5,defense.pivot_id,
                    f'{tf}M_NEAREST_OPPOSING_LIQUIDITY',min(defense.price,entry),max(defense.price,entry),
                    max(entry,objective.price),min(entry,objective.price),f,family='LEARNED_AUCTION_DIRECTION')


class LearnedLiquidityState:
    def __init__(self,ticks):self.markets={s:DirectionMarket(s,t) for s,t in ticks.items()}
    def observe(self,bars):
        if len({b.ts for b in bars.values()})!=1:raise ValueError('unequal observation times')
        plans=[]
        for s,b in bars.items():plans.extend(self.markets[s].observe(b))
        if not plans:return []
        factors={n:float(np.median([m.snapshot[n]['move'] for m in self.markets.values() if n in m.snapshot])) for n in HORIZONS}
        result=[]
        for p in plans:
            f=dict(p.features);s=int(p.side.value)
            for n in HORIZONS:
                f[f'market_move_{n}']=s*factors[n]
                f[f'relative_move_{n}']=f[f'move_{n}']-s*factors[n]
            result.append(replace(p,features=f))
        return result
