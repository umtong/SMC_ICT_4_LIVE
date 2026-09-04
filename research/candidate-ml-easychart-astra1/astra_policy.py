"""Astra: one boundary-control / first-return policy, shared by all four markets.

Source basis: EasyChart OB pp4-6; Fakeout/Trap pp6-7,11; channel pp8,11.
Machine translations: confirmed wick pivots, a range-scaled retest band, and a
closed one-minute response. These translations are hypotheses, not source quotes.
No symbol-specific parameters, target-R cap, indicator-vote list or daily limit.
"""
from __future__ import annotations
from collections import Counter, deque
from dataclasses import dataclass, field, asdict
import math
from typing import Any
import numpy as np
from domain import Candle, Side
from structure_v5 import CausalStructureBook

MINUTE=60_000_000_000
SYMBOLS=('BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT')
FEATURES=('context_progress','structure_direction','context_location','source_strength',
          'source_scale','acceptance','event_activity','event_progress','event_flow',
          'approach_efficiency','departure_progress','return_depth','return_activity',
          'return_flow','response_progress','response_flow','source_distance',
          'risk_range','cost_r','planned_rr','elapsed_wave','market_progress')

@dataclass(slots=True)
class Observation:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy: float
    quote: float=0.
    trades: int=0
    @property
    def delta(self): return 2*self.buy-self.volume

@dataclass(frozen=True,slots=True)
class Plan:
    plan_id: str
    causal_event_id: str
    symbol: str
    side: Side
    observed_time_ns: int
    interaction_time_ns: int
    entry: float
    stop: float
    target: float
    gross_rr: float
    source_level: float
    source_scale: int
    source_id: str
    target_kind: str
    zone_low: float
    zone_high: float
    departure_high: float
    departure_low: float
    features: dict[str,float]
    family: str='BOUNDARY_CONTROL_FIRST_RETURN'
    def record(self):
        d=asdict(self);d['side']=self.side.value
        d.update(d.pop('features'));return d

@dataclass(slots=True)
class Control:
    key: str
    source_id: str
    side: int
    scale: int
    level: float
    strength: float
    acceptance: bool
    started: int
    start_index: int
    stop: float
    zone_low: float
    zone_high: float
    unit: float
    event: Observation
    approach_efficiency: float
    prior_obstacle: float|None
    departure_high: float
    departure_low: float
    detached: bool=False
    returned: bool=False
    return_index: int=-1
    return_time: int=0
    return_open: float=0.
    return_volume: float=0.
    return_buy: float=0.
    departure_volume: float=0.
    departure_minutes: int=0

class WickMap(CausalStructureBook):
    """Reuse the project's causal pivot detector, not its perfect-line gates.

    The original book creates many permanent diagonal snapshots. Astra retains
    its wick-pivot confirmation algorithm but owns the lifetime of challenged
    liquidity, so an old line is not silently recycled as new evidence.
    """
    def observe(self, b:Observation):
        if self.bars and b.ts<=self.bars[-1].ts_close_ns:
            raise ValueError('non-increasing structure clock')
        self.bars.append(Candle(ts_close_ns=b.ts,open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume))
        return self._register_pivots(len(self.bars)-1)

class Market:
    def __init__(self,symbol:str,tick:float):
        self.symbol,self.tick=symbol,tick
        self.history: list[Observation]=[]
        self.five: list[Observation]=[]
        self.books={tf:WickMap(symbol,tf,tick,pivot_spans=(2,)) for tf in (5,15,60)}
        self.aggregate={tf:[] for tf in (5,15,60)}
        self.pending: dict[int,Control]={}
        self.stats=Counter()
        self.explanations=[]
        self.last_ts=0

    @staticmethod
    def merge(rows:list[Observation])->Observation:
        return Observation(rows[-1].ts,rows[0].open,max(b.high for b in rows),min(b.low for b in rows),rows[-1].close,
                           sum(b.volume for b in rows),sum(b.buy for b in rows),sum(b.quote for b in rows),sum(b.trades for b in rows))
    def unit(self)->float:
        return max(self.tick, float(np.median([b.high-b.low for b in self.five[-48:]]))) if self.five else self.tick
    def _explain(self,c:Control,reason:str,ts:int):
        self.stats[reason]+=1
        self.explanations.append({'symbol':self.symbol,'ts':ts,'event':c.key,'source':c.level,'side':c.side,'reason':reason})
    def _targets(self,side:int,price:float,ts:int):
        targets=[]
        for tf in (15,60):
            for p in self.books[tf]._active_pivots.values():
                if p.observed_time_ns>=ts:continue
                if (p.side=='HIGH') != (side>0):continue
                if side*(p.price-price)>self.tick:
                    targets.append(p.price)
        return targets
    def _feature_record(self,c:Control,b:Observation,entry:float,stop:float,target:float,market_progress:float):
        side=c.side;unit=c.unit;risk=abs(entry-stop)
        h=self.books[60].bars
        context_progress=side*(h[-1].close-h[-9].close)/max(self.tick,np.median([x.high-x.low for x in h[-24:]])) if len(h)>=9 else 0.
        p=self.books[15].pivots
        highs=[x.price for x in p if x.side=='HIGH'][-2:]
        lows=[x.price for x in p if x.side=='LOW'][-2:]
        structure_direction=0.
        if len(highs)==len(lows)==2:
            structure_direction=side*(np.sign(highs[-1]-highs[-2])+np.sign(lows[-1]-lows[-2]))/2
        context=self.books[15].bars[-32:]
        lo=min((x.low for x in context),default=entry);hi=max((x.high for x in context),default=entry)
        location=side*((entry-lo)/max(hi-lo,self.tick)-.5)*2
        ev=c.event;medvol=max(1e-12,float(np.median([x.volume for x in self.five[-48:]])))
        dep=side*((c.departure_high if side>0 else c.departure_low)-c.level)
        dep_minutes=max(1,c.departure_minutes);ret_minutes=max(1,len(self.history)-c.return_index)
        prev=self.history[-2] if len(self.history)>1 else b
        data=(context_progress,structure_direction,location,c.strength,math.log2(c.scale/5),float(c.acceptance),
              math.log1p(ev.volume/medvol),side*(ev.close-ev.open)/unit,side*ev.delta/max(ev.volume,1e-12),
              c.approach_efficiency,dep/unit,side*((c.departure_high if side>0 else c.departure_low)-entry)/max(dep,self.tick),
              math.log1p((c.return_volume/ret_minutes)/max(c.departure_volume/dep_minutes,1e-12)),
              side*(2*c.return_buy-c.return_volume)/max(c.return_volume,1e-12),side*(b.close-prev.close)/unit,
              side*b.delta/max(b.volume,1e-12),side*(entry-c.level)/unit,risk/unit,
              (.0005*(entry+stop)+.0001*(entry+stop))/risk,abs(target-entry)/risk,
              math.log1p((b.ts-c.started)/(c.scale*MINUTE)),side*market_progress)
        return {k:float(np.clip(v,-30.,30.)) for k,v in zip(FEATURES,data,strict=True)}

    def _advance(self,b:Observation,market_progress:float)->list[Plan]:
        output=[];i=len(self.history)-1
        prev=self.history[-2] if i else b
        for side,c in list(self.pending.items()):
            if b.ts<=c.started:continue
            if (side>0 and b.low<=c.stop) or (side<0 and b.high>=c.stop):
                self._explain(c,'control_invalidated',b.ts);del self.pending[side];continue
            if c.prior_obstacle is not None and ((side>0 and b.high>=c.prior_obstacle) or (side<0 and b.low<=c.prior_obstacle)):
                self._explain(c,'destination_spent_before_entry',b.ts);del self.pending[side];continue
            favourable=side*(b.close-c.level)
            band=(c.zone_high-c.zone_low)/2
            if not c.detached:
                c.departure_volume+=b.volume;c.departure_minutes+=1
                c.departure_high=max(c.departure_high,b.high);c.departure_low=min(c.departure_low,b.low)
                if favourable>band:
                    c.detached=True
                    self.stats['departure_confirmed']+=1
                continue
            touch=b.low<=c.zone_high if side>0 else b.high>=c.zone_low
            if not c.returned:
                if touch:
                    c.returned=True;c.return_index=i;c.return_time=b.ts;c.return_open=b.open
                    self.stats['first_return']+=1
                else:
                    c.departure_volume+=b.volume;c.departure_minutes+=1
                    c.departure_high=max(c.departure_high,b.high);c.departure_low=min(c.departure_low,b.low)
                    continue
            c.return_volume+=b.volume;c.return_buy+=b.buy
            # Closed-bar response after a public boundary is tested, not a prediction
            # that any long wick must reverse. Entry is never retrospectively at a low.
            response=b.close>prev.high if side>0 else b.close<prev.low
            if not response:continue
            entry=b.close
            if side*(entry-c.level)<0:continue
            natural_peak=c.departure_high if side>0 else c.departure_low
            targets=[(x,'OPPOSING_LIQUIDITY') for x in self._targets(side,entry,b.ts)]
            if side*(natural_peak-entry)>self.tick:targets.append((natural_peak,'DEPARTURE_EXTREME'))
            if not targets:
                self._explain(c,'no_structural_destination',b.ts);del self.pending[side];continue
            target,kind=min(targets,key=lambda x:side*(x[0]-entry))
            # Exit just before the observed frontier; no target-R lattice or cap.
            target-=side*self.tick
            risk=side*(entry-c.stop)
            rr=side*(target-entry)/risk if risk>0 else -1.
            if rr<1.-1e-9:
                self._explain(c,'first_response_geometry_below_one_r',b.ts);del self.pending[side];continue
            features=self._feature_record(c,b,entry,c.stop,target,market_progress)
            p=Plan(f'{c.key}:{b.ts}',c.key,self.symbol,Side.LONG if side>0 else Side.SHORT,b.ts,c.started,
                   entry,c.stop,target,rr,c.level,c.scale,c.source_id,kind,c.zone_low,c.zone_high,
                   c.departure_high,c.departure_low,features)
            output.append(p);self.stats['plan']+=1
            self._explain(c,'plan_emitted',b.ts);del self.pending[side]
        return output

    def _challenge(self,event:Observation):
        if len(self.five)<48:return
        u=self.unit();previous=self.five[-1]
        candidates=[]
        for tf in (15,60):
            for p in list(self.books[tf]._active_pivots.values()):
                if p.observed_time_ns>event.ts-5*MINUTE:continue
                hit=event.high>=p.price if p.side=='HIGH' else event.low<=p.price
                if not hit:continue
                self.books[tf]._active_pivots.pop(p.pivot_id,None)
                # A level must have been approached from its original side.
                from_inside=previous.close<p.price if p.side=='HIGH' else previous.close>p.price
                if not from_inside:continue
                side=1 if event.close>p.price else -1
                acceptance=(side>0)==(p.side=='HIGH')
                candidates.append((tf,p,side,acceptance))
        # Coincident nested pools are one challenge. Larger structural context owns
        # the boundary, rather than creating a separate trade for every pivot ID.
        for side in (1,-1):
            same=[x for x in candidates if x[2]==side]
            if not same:continue
            tf,p,_,acceptance=max(same,key=lambda x:(x[0],x[1].strength_ratio))
            stop=min(event.low,previous.low)-self.tick if side>0 else max(event.high,previous.high)+self.tick
            width=.25*u
            obs=self._targets(side,event.close,event.ts)
            obstacle=min(obs,key=lambda x:side*(x-event.close)) if obs else None
            approach=self.five[-6:]+[event]
            path=sum(abs(x.close-x.open) for x in approach)
            eff=side*(event.close-approach[0].open)/max(path,self.tick)
            key=f'{self.symbol}:CONTROL:{event.ts}:{side}'
            if side in self.pending:self._explain(self.pending[side],'superseded_by_new_boundary_event',event.ts)
            self.pending[side]=Control(key,p.pivot_id,side,tf,p.price,p.strength_ratio,acceptance,event.ts,len(self.history)-5,
                                      stop,p.price-width,p.price+width,u,event,eff,obstacle,event.high,event.low,
                                      detached=side*(event.close-p.price)>width,
                                      departure_volume=event.volume,departure_minutes=5)
            self.stats['public_boundary_challenge']+=1

    def observe(self,b:Observation,market_progress:float=0.)->list[Plan]:
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError(f'{self.symbol}: missing or duplicate minute')
        self.last_ts=b.ts;self.history.append(b)
        plans=self._advance(b,market_progress)
        for tf,rows in self.aggregate.items():
            rows.append(b)
            if b.ts//MINUTE%tf:continue
            if len(rows)==tf:
                combined=self.merge(rows)
                if tf==5:
                    self._challenge(combined);self.five.append(combined)
                self.books[tf].observe(combined)
            rows.clear()
        return plans

class AstraPolicy:
    def __init__(self,ticks:dict[str,float]):
        self.markets={s:Market(s,t) for s,t in ticks.items()}
        self.last_ts=0
    def observe(self,bars:dict[str,Observation])->list[Plan]:
        if set(bars)!=set(self.markets):raise ValueError('all configured symbols must be synchronized')
        timestamps={b.ts for b in bars.values()}
        if len(timestamps)!=1:raise ValueError('unequal observation clocks')
        ts=timestamps.pop()
        if self.last_ts and ts<=self.last_ts:raise ValueError('non-increasing policy clock')
        self.last_ts=ts
        moves=[]
        for s,b in bars.items():
            m=self.markets[s]
            if len(m.history)>=15:
                moves.append((b.close-m.history[-15].close)/max(m.unit(),m.tick))
        market_progress=float(np.median(moves)) if moves else 0.
        plans=[]
        for s in sorted(bars):plans.extend(self.markets[s].observe(bars[s],market_progress))
        return plans
