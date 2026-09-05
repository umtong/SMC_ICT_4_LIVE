"""Timing hypothesis: the first active response after an observed footprint test.

The source supplies the larger liquidity/structural hypothesis. Actual trade
sequence supplies the entry. A limit fill is not evidence of defense. Nor must
we wait for a full additional one-minute candle if the relevant response is
already observable. Five-second observations never create higher-timeframe
bars early. A still-forming minute is never passed to the structural policy.

A test accumulates actual volume and signed aggressive volume. Entry requires
price to reclaim that test's traded-value anchor, break the preceding micro
bar against the attack, and current aggressive flow to turn with the response.
The stop remains the entire source-event extreme, not a smaller micro wick.
"""
import numpy as np
from astra_policy import Plan,Observation,MINUTE
from domain import Side
from nested_control import NestedControlMarket,FEATURES as BASE_FEATURES

FEATURES=BASE_FEATURES+('test_flow','test_price_progress','test_activity','test_value_reclaimed',
                       'test_seconds','micro_response_flow','micro_response_range')
SECOND=1_000_000_000

class MicroResponseMarket(NestedControlMarket):
    def __init__(self,symbol,tick):
        super().__init__(symbol,tick)
        self.micro_previous=None;self.partial=[];self.micro_episode=None
        self.test=[];self.test_started=0
    def observe_context(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('missing completed context minute')
        self._fresh_challenge(b);closed=self._update(b);e=self.episode
        if e is None or e.finished:return
        if not e.control_time:
            e.extreme=max(e.extreme,b.high) if e.source_kind>0 else min(e.extreme,b.low)
            if 5 in closed and len(self.five)>=25:self._form_control(e,self.five[-1])
    def observe(self,b):
        if self.micro_previous and b.ts-self.micro_previous.ts!=5*SECOND:raise ValueError('missing micro clock')
        previous=self.micro_previous;self.micro_previous=b
        self.partial.append(b)
        if b.ts%MINUTE==0:
            if len(self.partial)==12:self.observe_context(self.aggregate(self.partial))
            self.partial=[]
        e=self.episode
        if e is None or e.finished or b.ts<=e.control_time or e.control_time==0:return None
        if self.micro_episode!=e.key:
            self.micro_episode=e.key;self.test=[];self.test_started=0
        side=-e.source_kind
        if self.control[60].direction!=side:
            e.finished=True;self.stats['micro_higher_control_changed']+=1;return None
        if (side>0 and b.low<=e.stop) or (side<0 and b.high>=e.stop):
            e.finished=True;self.stats['micro_origin_failed']+=1;return None
        touch=b.low<=e.zone_high and b.high>=e.zone_low
        if not self.test:
            e.departure=max(e.departure,b.high) if side>0 else min(e.departure,b.low)
            if not touch:return None
            self.test_started=b.ts;self.stats['micro_first_test']+=1
        elif side*(b.close-e.departure)>=0:
            e.finished=True;self.stats['micro_wave_ended_without_entry']+=1;return None
        self.test.append(b)
        if previous is None or len(self.test)<2:return None
        vol=sum(x.volume for x in self.test);quote=sum(x.quote for x in self.test)
        if vol<=0:return None
        value=quote/vol
        active=side*b.delta>0
        price_break=b.close>previous.high if side>0 else b.close<previous.low
        value_reclaimed=side*(b.close-value)>self.tick
        nearby=touch or (previous.low<=e.zone_high and previous.high>=e.zone_low)
        if not (active and price_break and value_reclaimed and nearby):return None
        entry=b.close;risk=side*(entry-e.stop)
        levels=[(e.departure,'FIRST_RESPONSE_EXTREME'),(e.reference_other,'PARENT_OPPOSING_SWING')]
        for tf,book in self.books.items():
            if tf<15:continue
            for p in book.pivots[-40:]:
                if p.pivot_id not in self.touched and p.side==('HIGH' if side>0 else 'LOW'):
                    levels.append((p.price,f'UNSPENT_{tf}M_SWING'))
        for z in self.zones:
            if z.live and z.side!=side:
                if z.low<=entry<=z.high:return None
                levels.append((z.low if side>0 else z.high,'OPPOSING_VALIDATED_CONTROL'))
        levels=[(x,k) for x,k in levels if side*(x-entry)>self.tick]
        if risk<=self.tick or not levels:return None
        target,kind=min(levels,key=lambda x:side*(x[0]-entry));target-=side*self.tick
        rr=side*(target-entry)/risk
        if rr<1:
            self.stats['micro_first_obstacle_less_than_one_r']+=1;return None
        f={k:float('nan') for k in FEATURES};f.update(e.impulse_context);unit=f.pop('unit')
        f.update(planned_rr=rr,cost_r=.0012*entry/risk,risk_bps=10000*risk/entry,risk_range=risk/unit,
            obstacle_distance=side*(target-entry)/unit,pullback_depth=side*(e.departure-entry)/abs(e.departure-e.stop),
            test_flow=side*sum(x.delta for x in self.test)/vol,
            test_price_progress=side*(b.close-self.test[0].open)/unit,
            test_activity=vol/max(self.current_volume*len(self.test)/12,1e-12),
            test_value_reclaimed=side*(b.close-value)/unit,
            test_seconds=(b.ts-self.test_started)/SECOND,
            micro_response_flow=side*b.delta/max(b.volume,1e-12),
            micro_response_range=side*(b.close-b.open)/unit,
            response_age=(b.ts-e.control_time)/(5*MINUTE))
        e.finished=True;self.stats['micro_plans']+=1
        return Plan(f'{self.symbol}:MICRO:{e.key}:{b.ts}',f'{self.symbol}:LQC:{e.key}',self.symbol,
            Side.LONG if side>0 else Side.SHORT,b.ts,e.started,entry,e.stop,target,rr,e.level,e.scale,e.key,kind,
            e.zone_low,e.zone_high,e.departure if side>0 else e.stop,e.stop if side>0 else e.departure,f,
            family='NESTED_LIQUIDITY_MICRO_RESPONSE')

class MicroResponsePolicy:
    def __init__(self,ticks):self.markets={s:MicroResponseMarket(s,t) for s,t in ticks.items()}
    def observe(self,bars):
        if set(bars)!=set(self.markets) or len({x.ts for x in bars.values()})!=1:raise ValueError('unsynchronized micro universe')
        return [p for s,b in bars.items() if (p:=self.markets[s].observe(b)) is not None]
