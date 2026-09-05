"""Exceptional public-pool excursion and recovery of local control.

This is not another OB/FVG filter. EasyChart's liquidity-sweep/reclaim relation
supplies the directional event; the external transient-impact hypothesis adds
pre-shock transaction VWAP as a frozen inventory-value destination. OI and
premium repair describe position contraction, not proof of actual liquidations.

The 3-sigma five-minute shock definition and 240-minute volatility memory are
research assumptions. All scales are shared across symbols. Future observations
are unavailable to this incremental policy.
"""
from __future__ import annotations
from collections import Counter,deque
from dataclasses import dataclass
import math
import numpy as np
from astra_policy import Observation,Plan,MINUTE
from policy import Frame
from domain import Side

FEATURES=('shock_sigma','shock_pressure','shock_activity','recovery_fraction',
          'response_pressure','response_progress','basis_stress','basis_repair',
          'oi_change','spot_confirmation','peer_response','context_direction',
          'source_scale','risk_range','cost_r','planned_rr')

@dataclass
class Washout:
    key:str
    side:int
    detected:int
    started:int
    source:float
    source_key:str
    scale:int
    extreme:float
    anchor:float
    unit:float
    shock_sigma:float
    pressure:float
    activity:float
    premium_origin:float
    premium_extreme:float
    oi:float
    spot_confirmation:float
    emitted:bool=False

class WashoutMarket:
    def __init__(self,symbol,tick):
        self.symbol=symbol;self.tick=tick
        self.history=deque(maxlen=1441)
        self.frames={n:Frame(n) for n in (5,15,60)}
        self.variance=0.;self.count=0;self.last_ts=0
        self.touches={};self.active=None;self.stats=Counter();self.explanations=[]

    def _sources(self,rows,start,b):
        sources=[]
        for tf in (15,60):
            for z in self.frames[tf].pivots:
                touch=self.touches.get(z.key)
                if z.born<start and touch is not None and start<touch<=b.ts:
                    sources.append((z.kind,z.price,z.key,tf,touch))
        # A completed pre-shock one-hour range is another public reference,
        # fixed BEFORE the impulse, not an aligned four-hour clock box.
        prior=rows[-65:-5]
        if len(prior)==60:
            for kind,price in ((-1,min(x.low for x in prior)),(1,max(x.high for x in prior))):
                crossing=next((v.ts for v in rows[-5:] if (v.low<price if kind<0 else v.high>price)),None)
                if crossing is not None:sources.append((kind,price,f'HOUR:{start}:{kind}',60,crossing))
        return sources

    def _seed(self,b,x,rows,sigma):
        if len(rows)<241 or sigma<=0:return
        first=rows[-6]
        change=math.log(b.close/first.close)
        zscore=abs(change)/sigma
        if zscore<3.:return
        direction=1 if change>0 else -1;side=-direction
        start=first.ts
        sources=[z for z in self._sources(rows,start,b) if z[0]==direction]
        if not sources:return
        _,level,key,tf,touched=max(sources,key=lambda z:(z[3],z[4]))
        prior=rows[-21:-6]
        base_volume=sum(v.volume for v in prior)
        if base_volume<=0:return
        anchor=sum(v.quote for v in prior)/base_volume
        impulse=rows[-5:]
        extreme=max(v.high for v in impulse) if direction>0 else min(v.low for v in impulse)
        if side*(anchor-extreme)<=0:return
        volume=sum(v.volume for v in impulse)
        pressure=sum(v.delta for v in impulse)/max(volume,1e-12)
        unit=max(self.tick,first.close*sigma)
        premium=x.get('x_premium',float('nan'))
        premium_change=x.get('x_premium_change',float('nan'))
        self.active=Washout(f'{self.symbol}:WASHOUT:{key}:{touched}',side,b.ts,touched,level,key,tf,extreme,anchor,unit,
             zscore,side*pressure,(volume/5)/max(base_volume/15,1e-12),premium-premium_change,premium,
             x.get('x_oi_change_15',float('nan')),side*x.get('x_spot_move_15',float('nan')))
        self.stats['exceptional_public_excursion']+=1

    def _advance(self,b,previous,x,peer):
        e=self.active
        if e is None or b.ts<=e.detected:return []
        side=e.side
        e.extreme=min(e.extreme,b.low) if side>0 else max(e.extreme,b.high)
        recovered=b.high>=e.anchor if side>0 else b.low<=e.anchor
        settled=any(z.born>e.detected and z.pivot_time>e.detected and z.kind==-side
                    and side*(z.price-e.source)<0 for z in self.frames[15].pivots[-6:])
        if recovered or settled:
            self.stats['value_recovered' if recovered else 'new_accepted_outside_swing']+=1
            self.active=None
            return []
        if e.emitted:return []
        reclaimed=side*(b.close-e.source)>0
        response=b.close>previous.high if side>0 else b.close<previous.low
        if not reclaimed or not response:return []
        stop=e.extreme-side*self.tick
        objectives=[e.anchor]
        for tf in (5,15,60):
            objectives.extend(z.price for z in self.frames[tf].pivots
                if z.born<b.ts and z.kind==side and z.key not in self.touches)
        ahead=[p for p in objectives if side*(p-b.close)>self.tick]
        if not ahead:return []
        target=min(ahead,key=lambda p:side*(p-b.close))-side*self.tick
        risk=side*(b.close-stop);reward=side*(target-b.close)
        if risk<=self.tick or reward<risk:
            self.stats['response_without_one_r']+=1
            return []
        initial_basis=e.premium_extreme-e.premium_origin
        current_basis=x.get('x_premium',float('nan'))-e.premium_origin
        f=dict(shock_sigma=e.shock_sigma,shock_pressure=e.pressure,shock_activity=math.log1p(e.activity),
               recovery_fraction=side*(b.close-e.extreme)/max(side*(e.anchor-e.extreme),self.tick),
               response_pressure=side*b.delta/max(b.volume,1e-12),response_progress=side*(b.close-b.open)/e.unit,
               basis_stress=-side*initial_basis,
               basis_repair=side*(current_basis-initial_basis),oi_change=e.oi,
               spot_confirmation=e.spot_confirmation,peer_response=side*peer,
               context_direction=side*self.frames[60].direction(),source_scale=math.log2(e.scale/5),
               risk_range=risk/e.unit,cost_r=.0006*(b.close+stop)/risk,planned_rr=reward/risk)
        e.emitted=True;self.stats['plan']+=1
        return [Plan(f'{e.key}:{b.ts}',e.key,self.symbol,Side.LONG if side>0 else Side.SHORT,
            b.ts,e.started,b.close,stop,target,reward/risk,e.source,e.scale,e.source_key,
            'PRE_SHOCK_TRANSACTION_VALUE_OR_FIRST_OPPOSING_SWING',e.extreme,e.source,
            max(e.anchor,e.extreme),min(e.anchor,e.extreme),{k:float(v) for k,v in f.items()},family='INVENTORY_WASHOUT_RECLAIM')]

    def observe(self,b,x,peer):
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError('non-contiguous washout observation clock')
        self.last_ts=b.ts
        previous=self.history[-1] if self.history else b
        sigma=math.sqrt(max(self.variance,1e-16)*5)
        self.history.append(b);rows=list(self.history)
        for tf in (5,15,60):
            for z in self.frames[tf].pivots:
                if z.born<b.ts and z.key not in self.touches and (b.high>=z.price if z.kind>0 else b.low<=z.price):
                    self.touches[z.key]=b.ts
        plans=self._advance(b,previous,x,peer)
        if self.active is None:self._seed(b,x,rows,sigma)
        for frame in self.frames.values():frame.append(b)
        change=math.log(b.close/previous.close)
        self.count+=1
        alpha=2/241
        self.variance=(1-alpha)*self.variance+alpha*change*change
        return plans

class WashoutPolicy:
    def __init__(self,ticks):
        self.markets={s:WashoutMarket(s,t) for s,t in ticks.items()};self.last_ts=0
    def observe(self,bars,extras):
        if set(bars)!=set(self.markets) or set(extras)!=set(bars) or len({b.ts for b in bars.values()})!=1:
            raise ValueError('incomplete synchronized washout inputs')
        ts=next(iter(bars.values())).ts
        if ts<=self.last_ts:raise ValueError('non-increasing washout time')
        self.last_ts=ts
        moves={s:math.log(b.close/list(self.markets[s].history)[-5].close)/max(math.sqrt(self.markets[s].variance*5),1e-8)
               for s,b in bars.items() if len(self.markets[s].history)>=5}
        output=[]
        for s in sorted(bars):
            peers=[v for k,v in moves.items() if k!=s]
            output+=self.markets[s].observe(bars[s],extras[s],float(np.median(peers)) if peers else 0.)
        return output
