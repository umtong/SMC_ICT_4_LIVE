"""Nested liquidity response, using one persistent structural-control state.

EasyChart source: a large structure supplies direction and small footprints
supply price; a footprint alone is not a directional thesis. This implementation
is a research translation, not a claim that the source specifies these pivots.

Structural corrections to the failed versions:
* Higher control is the protected origin of an accepted break, not a vote of
  the last two wick changes. A corrective lower-timeframe direction is allowed.
* Only a footprint caused by an actual higher-liquidity response has an opposing
  control role. Every arbitrary two-candle colour change is NOT a wall.
* Prior traded value is contextual information, not automatically resistance.
* The first corrective wave ends when its departure is reclaimed; a rejected
  entry is never retried hours later away from the original entry location.
"""
from dataclasses import dataclass
import numpy as np
from astra_policy import Plan,MINUTE
from domain import Side
from liquidity_control import LiquidityPolicy,PriceZone,FEATURES as BASE_FEATURES
from local_response import LocalResponseMarket

FEATURES=BASE_FEATURES+('higher_protected_distance','higher_control_age','response_age')

@dataclass
class ProtectedControl:
    direction:int=0
    protected:float=0.
    protected_id:str=''
    changed:int=0
    observed:int=0
    def __post_init__(self):self.spent=set()
    def observe(self,book,b):
        hi=[p for p in book.pivots[-40:] if p.side=='HIGH' and p.observed_time_ns<=b.ts]
        lo=[p for p in book.pivots[-40:] if p.side=='LOW' and p.observed_time_ns<=b.ts]
        if not hi or not lo:return
        old=self.direction
        if self.direction and self.direction*(b.close-self.protected)<0:
            self.direction=-self.direction
            p=hi[-1] if self.direction<0 else lo[-1]
            self.protected=p.price;self.protected_id=p.pivot_id;self.changed=b.ts
        for side,levels,origins in ((1,hi,lo),(-1,lo,hi)):
            p=levels[-1]
            if p.pivot_id in self.spent or side*(b.close-p.price)<=0:continue
            self.spent.add(p.pivot_id)
            q=origins[-1]
            if side*(p.price-q.price)<=0:continue
            if self.direction not in (0,side):continue
            if self.direction==0 or side*(q.price-self.protected)>0:
                self.protected=q.price;self.protected_id=q.pivot_id
            if self.direction==0:self.changed=b.ts
            self.direction=side
        self.observed=b.ts

class NestedControlMarket(LocalResponseMarket):
    def __init__(self,symbol,tick):
        super().__init__(symbol,tick)
        self.control={tf:ProtectedControl() for tf in (15,60)}
        self.observation_rows=[]
    def _zones(self,tf):
        # Contextual control footprints are recorded by _form_control instead.
        return
    def _update(self,b):
        closed=super()._update(b)
        for tf in (15,60):
            if tf in closed:self.control[tf].observe(self.books[tf],self.frames[tf][-1])
        self.zones=[z for z in self.zones if z.live]
        return closed
    def _form_control(self,e,b):
        before=e.control_time
        super()._form_control(e,b)
        if before or not e.control_time:return
        side=-e.source_kind
        key=f'VALIDATED:{e.key}:{b.ts}'
        self.zones.append(PriceZone(key,side,e.zone_low,e.zone_high,e.stop,b.ts,5))
        higher=self.control[60]
        if higher.direction!=side:
            e.finished=True;self.stats['local_response_against_higher_control']+=1;return
        e.impulse_context['context_15']=side*self.control[15].direction
        e.impulse_context['context_60']=side*higher.direction
        e.impulse_context['higher_protected_distance']=side*(b.close-higher.protected)/e.impulse_context['unit']
        e.impulse_context['higher_control_age']=(b.ts-higher.changed)/(60*MINUTE)
        self.stats['nested_response']+=1
    def observe(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('incomplete minute sequence')
        self._fresh_challenge(b);closed=self._update(b);e=self.episode
        if e is None or e.finished:return None
        side=-e.source_kind
        if not e.control_time:
            e.extreme=max(e.extreme,b.high) if e.source_kind>0 else min(e.extreme,b.low)
            if 5 in closed and len(self.five)>=25:self._form_control(e,self.five[-1])
            return None
        if self.control[60].direction!=side:
            e.finished=True;self.stats['higher_control_changed']+=1;return None
        if (side>0 and b.low<=e.stop) or (side<0 and b.high>=e.stop):
            e.finished=True;self.stats['origin_failed']+=1;return None
        if e.returned<0:
            # The actual first impulse may extend before the first retracement.
            e.departure=max(e.departure,b.high) if side>0 else min(e.departure,b.low)
            if b.low<=e.zone_high and b.high>=e.zone_low:
                e.returned=len(self.history)-1;self.stats['first_return']+=1
            else:return None
        elif side*(b.close-e.departure)>=0:
            e.finished=True;self.stats['first_corrective_wave_ended_without_entry']+=1;return None
        previous=self.history[-2]
        touches=b.low<=e.zone_high and b.high>=e.zone_low
        previous_touch=previous.low<=e.zone_high and previous.high>=e.zone_low
        response=(b.close>previous.high if side>0 else b.close<previous.low)
        if not response or not (touches or previous_touch):return None
        if side*(b.close-(e.zone_low+e.zone_high)/2)<=0:return None
        entry=b.close;risk=side*(entry-e.stop)
        levels=[(e.departure,'FIRST_RESPONSE_EXTREME'),(e.reference_other,'PARENT_OPPOSING_SWING')]
        for tf,book in self.books.items():
            if tf<15:continue
            for p in book.pivots[-40:]:
                if p.pivot_id not in self.touched and p.side==('HIGH' if side>0 else 'LOW'):
                    levels.append((p.price,f'UNSPENT_{tf}M_SWING'))
        for z in self.zones:
            if not z.live or z.side==side:continue
            if z.low<=entry<=z.high:
                self.stats['opposing_control_at_entry']+=1;return None
            levels.append((z.low if side>0 else z.high,'OPPOSING_VALIDATED_CONTROL'))
        levels=[(p,k) for p,k in levels if side*(p-entry)>self.tick]
        if risk<=self.tick or not levels:return None
        target,kind=min(levels,key=lambda x:side*(x[0]-entry));target-=side*self.tick
        rr=side*(target-entry)/risk
        if rr<1:
            self.stats['first_obstacle_less_than_one_r']+=1
            self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'nested_first_obstacle',
                'entry':entry,'stop':e.stop,'target':target,'rr':rr,'source':e.key})
            return None
        f=dict(e.impulse_context);unit=f.pop('unit');a=self.history[e.control_index+1:]
        vol=sum(x.volume for x in a);travel=sum(x.high-x.low for x in a)
        local=self.history[-60:];low=min(x.low for x in local);high=max(x.high for x in local)
        f.update(location_60=2*side*((entry-low)/max(high-low,self.tick)-.5),
            pullback_depth=side*(e.departure-entry)/max(abs(e.departure-e.stop),self.tick),
            pullback_efficiency=side*(entry-e.departure)/max(travel,self.tick),
            pullback_activity=vol/max(len(a)*self.current_volume,1e-12),
            pullback_flow=side*sum(x.delta for x in a)/max(vol,1e-12),
            pullback_duration=(b.ts-e.control_time)/(5*MINUTE),
            response_range=side*(b.close-b.open)/self.current_unit,
            response_flow=side*b.delta/max(b.volume,1e-12),response_activity=b.volume/self.current_volume,
            innovation_fast=side*self.impact.fast,innovation_slow=side*self.impact.slow,
            impact_slope=float(self.impact.theta[1]),risk_range=risk/unit,cost_r=.0012*entry/risk,
            planned_rr=rr,obstacle_distance=side*(target-entry)/unit,risk_bps=10000*risk/entry,
            auction_value_distance=side*(e.reference_value-entry)/unit,
            participation=(b.volume/max(b.trades,1))/max(self.current_volume/max(np.mean([x.trades for x in local]),1),1e-12),
            response_age=(b.ts-e.control_time)/(5*MINUTE))
        e.finished=True;self.stats['plans']+=1
        region=self.history[e.start_index:]
        return Plan(f'{self.symbol}:NESTED:{e.key}:{b.ts}',f'{self.symbol}:LQC:{e.key}',self.symbol,
            Side.LONG if side>0 else Side.SHORT,b.ts,e.started,entry,e.stop,target,rr,e.level,e.scale,e.key,kind,
            e.zone_low,e.zone_high,max(x.high for x in region),min(x.low for x in region),f,
            family='NESTED_LIQUIDITY_RESPONSE')

class NestedControlPolicy(LiquidityPolicy):
    def __init__(self,ticks):self.markets={s:NestedControlMarket(s,t) for s,t in ticks.items()}
