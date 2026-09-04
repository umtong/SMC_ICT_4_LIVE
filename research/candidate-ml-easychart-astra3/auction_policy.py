"""Liquidity control, not independent OB/FVG trading strategies.

EasyChart translation: a previously known structure is challenged; an OB/FVG
impulse shows whether that challenge gained acceptance or was rejected. Only
its first return is actionable. Entry is at the observed origin price, not after
chasing another confirmation impulse. The opposite structure/wave is the target.
"""
from __future__ import annotations
from dataclasses import dataclass,replace
from collections import deque
import math
import numpy as np
from policy import Market as GeometryMarket,Level,Challenge,Origin,FEATURES as BASE_FEATURES
from astra_policy import Observation,MINUTE,SYMBOLS

FEATURES=BASE_FEATURES+('acceptance','control_frame','attack_flow','attack_progress',
    'attack_activity','response_progress','response_flow','response_activity',
    'retracement_flow','retracement_efficiency','liquidity_origin_overlap')

@dataclass(slots=True)
class Pulse:
    origin: Origin
    acceptance: bool
    frame: int
    attack: list
    response: list
    return_rows: list
    zone: dict

class AuctionMarket(GeometryMarket):
    def __init__(self,symbol,tick,external=None):
        super().__init__(symbol,tick,external)
        self.attacks=deque(maxlen=96)
        self.pulses={}
        self.owned=set()
    def _liquidity(self,b,prev):
        hits=[]
        for frame in self.frames.values():
            for z in frame.levels:
                if z.consumed or z.born>=b.ts:continue
                touched=b.high>=z.price if z.kind>0 else b.low<=z.price
                if not touched:continue
                z.consumed=True
                if prev.close<z.price if z.kind>0 else prev.close>z.price:hits.append(z)
        for c in self.attacks:
            c.high=max(c.high,b.high);c.low=min(c.low,b.low)
        if len(self.five)<48:return
        for kind in (-1,1):
            same=[z for z in hits if z.kind==kind]
            if not same:continue
            z=max(same,key=lambda x:(x.tf,x.strength))
            self.attacks.append(Challenge(z.key,z,b.ts,b.high,b.low,b.volume,b.buy,
                     max(np.mean([v.volume for v in self.history[-61:-1]]),1e-12),self.unit(),None))
            self.stats['liquidity_challenge']+=1
    @staticmethod
    def _flow(rows,side):
        return side*sum(v.delta for v in rows)/max(sum(v.volume for v in rows),1e-12)
    def _formation(self,tf,b):
        born=[z for z in self.zones if z['tf']==tf and z['born']==b.ts]
        for side in (-1,1):
            choices=[]
            for z in born:
                if z['side']!=side:continue
                # A formed origin must have detached before a first return.
                if side*(b.close-(z['high'] if side>0 else z['low']))<=0:continue
                for c in self.attacks:
                    if c.started<b.ts-3*tf*MINUTE or c.started>b.ts:continue
                    if side*(b.close-c.level.price)<=0:continue
                    if (c.key,side) in self.owned:continue
                    overlap=z['low']<=c.level.price<=z['high']
                    choices.append((c,z,overlap))
            if not choices:continue
            # Nested footprints have one owner. Prefer a price origin actually
            # overlapping the challenged structure, then its larger structure.
            c,z,overlap=max(choices,key=lambda q:(q[2],q[0].level.tf,q[1]['key'].startswith('OB'),q[0].started))
            key=(c.key,side)
            old=self.pulses.get(side)
            if old is not None and old.origin.parent.key==c.key:continue
            self.owned.add(key)
            parent=replace(c)
            attack=[v for v in self.history if c.started-tf*MINUTE<v.ts<=c.started]
            response=[v for v in self.history if c.started<v.ts<=b.ts]
            if not response:response=[self.history[-1]]
            parent.volume=sum(v.volume for v in attack)
            parent.buy=sum(v.buy for v in attack)
            stop=min(z['stop'],c.low-self.tick) if side>0 else max(z['stop'],c.high+self.tick)
            original=Origin(c.key,parent,side,b.ts,z['low'],z['high'],stop,b.high,b.low,None,int(overlap))
            self.pulses[side]=Pulse(original,c.level.kind==side,tf,attack,response,[],z)
            context=self._context(c)
            if context is not None:self.contexts[parent.key]=context
            self.stats['acceptance_control' if c.level.kind==side else 'rejection_control']+=1
    def _returns(self,b,market):
        plans=[]
        for side,pulse in list(self.pulses.items()):
            o=pulse.origin
            if b.ts<=o.born:continue
            if b.low<=o.stop if side>0 else b.high>=o.stop:
                self.stats['control_invalidated']+=1;del self.pulses[side];continue
            if not o.returned:
                if b.low>o.high if side>0 else b.high<o.low:
                    o.departure_high=max(o.departure_high,b.high)
                    o.departure_low=min(o.departure_low,b.low)
                    continue
                o.returned=True;o.return_time=b.ts
                self.stats['first_origin_return']+=1
            elif b.high>=o.departure_high if side>0 else b.low<=o.departure_low:
                self.stats['return_wave_finished']+=1;del self.pulses[side];continue
            pulse.return_rows.append(b)
            o.return_volume+=b.volume;o.return_count+=1
            # The impulse already confirmed control. Waiting for a second
            # impulse moves entry away from the source's preselected location.
            if not o.low<=b.close<=o.high:continue
            peak=o.departure_high if side>0 else o.departure_low
            ahead=[v for v in self.targets(side,b.close,b.ts)+[peak] if side*(v-b.close)>self.tick]
            if not ahead:continue
            target=min(ahead,key=lambda v:side*(v-b.close))
            plan=self._plan(o.parent,b,side,o.stop,target,market,o)
            if plan is None:continue
            u=o.parent.unit;a=pulse.attack;r=pulse.response;rt=pulse.return_rows
            baseline=o.parent.baseline_volume
            path=sum(abs(v.close-v.open) for v in rt)
            f=dict(plan.features)
            f.update(acceptance=float(pulse.acceptance),control_frame=math.log2(pulse.frame/5),
                     attack_flow=self._flow(a,side),attack_progress=side*(a[-1].close-a[0].open)/u,
                     attack_activity=math.log1p(sum(v.volume for v in a)/max(len(a)*baseline,1e-12)),
                     response_progress=side*(r[-1].close-r[0].open)/u,response_flow=self._flow(r,side),
                     response_activity=math.log1p(sum(v.volume for v in r)/max(len(r)*baseline,1e-12)),
                     retracement_flow=self._flow(rt,side),
                     retracement_efficiency=side*(rt[-1].close-rt[0].open)/max(path,self.tick),
                     liquidity_origin_overlap=float(o.low<=o.parent.level.price<=o.high))
            family='CONTROL_ACCEPTANCE_RETURN' if pulse.acceptance else 'CONTROL_REJECTION_RETURN'
            plans.append(replace(plan,features=f,family=family))
            self.stats['plan_emitted']+=1
            del self.pulses[side]
        return plans
    def observe(self,b,market):
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError('non-contiguous auction observation')
        self.last_ts=b.ts
        prev=self.history[-1] if self.history else b
        self.history.append(b)
        self.bases.append(10000*(b.close/self.external(self.symbol,b.ts)-1) if self.external else float('nan'))
        self._update_zones(b)
        self._liquidity(b,prev)
        plans=self._returns(b,market) if len(self.history)>240 else []
        for tf,frame in self.frames.items():
            x=frame.append(b)
            if x is None:continue
            self._new_zones(tf)
            if tf in (5,15) and len(self.history)>240:self._formation(tf,x)
        return plans

class LiquidityPolicy:
    def __init__(self,ticks,external=None):
        self.markets={s:AuctionMarket(s,t,external) for s,t in ticks.items()}
        self.last_ts=0
    def observe(self,bars):
        if set(bars)!=set(self.markets):raise ValueError('incomplete universe observation')
        timestamps={b.ts for b in bars.values()}
        if len(timestamps)!=1:raise ValueError('unequal market clocks')
        ts=timestamps.pop()
        if ts<=self.last_ts:raise ValueError('non-increasing clock')
        self.last_ts=ts
        market={}
        for n in (5,15,60):
            values=[(b.close-self.markets[s].history[-n].close)/self.markets[s].unit()
                    for s,b in bars.items() if len(self.markets[s].history)>=n]
            market[n]=float(np.median(values)) if values else 0.
        return [p for s in sorted(bars) for p in self.markets[s].observe(bars[s],market)]
