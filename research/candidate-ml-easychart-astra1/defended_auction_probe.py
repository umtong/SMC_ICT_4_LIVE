"""A directional intervention test at previously observed liquidity.

Repeated aggressive flow is not itself alpha: market impact often adapts to
predictable flow (Taranto/Bormetti/Lillo, arXiv:1403.0842). The test here asks a
more specific question: after a known price boundary is challenged, can a second
separate adverse pressure run, with at least the first run's volume, extend the
auction beyond ordinary pre-event price noise? If it cannot, a close reclaiming
the adverse runs' traded value is the first executable control response.

This is an explicit hypothesis, not identification of an institution or hidden
orders. One-minute net aggressor flow separates pressure runs; actual adverse
aggressor volume weights their traded-value approximation. No FVG constellation,
second breakout confirmation, trained score threshold or fixed-R objective is
required. Existing structural prices define both the invalidation and target.
"""
from collections import Counter,deque
from dataclasses import dataclass,replace
import math
import numpy as np
from astra_policy import Observation,Plan,WickMap,MINUTE
from auction_control_survival import aggregate
from domain import Side

FEATURES=('source_scale','source_prominence','effort_ratio','extension_noise',
          'value_reclaim','event_minutes','first_effort','response_flow',
          'risk_bps','risk_range','cost_r','planned_rr','market_move','relative_move')

@dataclass
class Probe:
    volume:float=0.
    value:float=0.
    low:float=float('inf')
    high:float=-float('inf')
    bars:int=0
    def add(self,b,adverse):
        price=b.quote/b.volume if b.quote>0 and b.volume>0 else (b.high+b.low+b.close)/3
        self.volume+=adverse;self.value+=adverse*price
        self.low=min(self.low,b.low);self.high=max(self.high,b.high);self.bars+=1

@dataclass
class Interaction:
    key:str;side:int;started:int;level:float;scale:int;prominence:float
    noise:float;unit:float;baseline:float;low:float;high:float
    first:Probe|None=None
    active:Probe|None=None
    consumed:bool=False

class ProbeMarket:
    def __init__(self,symbol,tick):
        self.symbol=symbol;self.tick=tick;self.history=[]
        self.books={tf:WickMap(symbol,tf,tick,pivot_spans=(2,)) for tf in (5,15,60,240)}
        self.buckets={tf:[] for tf in self.books}
        self.retired=set();self.events={};self.stats=Counter();self.explanations=[]
        self.ranges=deque(maxlen=60);self.volumes=deque(maxlen=60)

    def _new_events(self,b,noise,unit,baseline):
        candidates={1:[],-1:[]}
        for tf,book in self.books.items():
            for p in book.pivots[-24:]:
                if p.pivot_id in self.retired or p.observed_time_ns>=b.ts:continue
                crossed=b.low<p.price-self.tick if p.side=='LOW' else b.high>p.price+self.tick
                if not crossed:continue
                self.retired.add(p.pivot_id)
                s=1 if p.side=='LOW' else -1;candidates[s].append((tf,p))
        for s,items in candidates.items():
            if not items:continue
            tf,p=max(items,key=lambda x:(x[0],x[1].strength_ratio))
            old=self.events.get(s)
            if old is not None and not old.consumed:
                # Multiple adjacent stops taken by one excursion remain one event.
                self.stats['additional_liquidity_same_event']+=1
                continue
            key=f'{self.symbol}:PROBE:{p.pivot_id}:{b.ts}'
            self.events[s]=Interaction(key,s,b.ts,p.price,tf,p.strength_ratio,
                noise,unit,baseline,b.low,b.high)
            self.stats['liquidity_interactions']+=1

    def observe(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('non-contiguous market observations')
        noise=max(float(np.median(self.ranges)) if self.ranges else b.high-b.low,2*self.tick)
        unit=max(float(np.mean(self.ranges)) if self.ranges else b.high-b.low,2*self.tick)
        baseline=max(float(np.mean(self.volumes)) if self.volumes else b.volume,1e-12)
        self.history.append(b)
        if len(self.history)>=1440:self._new_events(b,noise,unit,baseline)
        self.ranges.append(b.high-b.low);self.volumes.append(b.volume)
        for tf in sorted(self.books,reverse=True):
            self.buckets[tf].append(b)
            if b.ts//MINUTE%tf==0:
                a=self.buckets[tf];self.buckets[tf]=[]
                if len(a)==tf:self.books[tf].observe(aggregate(a))
        output=[]
        for s,e in self.events.items():
            if e.consumed:continue
            e.low=min(e.low,b.low);e.high=max(e.high,b.high)
            # Once the market has left the interaction neighborhood, this is no
            # longer another test of the same displayed price. Do not resurrect.
            if abs(b.close-e.level)>3*e.noise:
                e.consumed=True;self.stats['auction_left_interaction']+=1;continue
            adverse_flow=-s*b.delta
            adverse_volume=b.volume-b.buy if s>0 else b.buy
            if adverse_flow>0:
                if e.active is None:e.active=Probe()
                e.active.add(b,adverse_volume)
                continue
            completed=e.active;e.active=None
            if completed is None or completed.volume<=0:continue
            if e.first is None:
                e.first=completed;self.stats['first_pressure_run']+=1;continue
            first=e.first
            extension=first.low-completed.low if s>0 else completed.high-first.high
            effort=completed.volume/first.volume
            if completed.volume<first.volume:
                self.stats['second_effort_smaller']+=1
                continue
            if extension>e.noise:
                e.consumed=True;self.stats['adverse_pressure_progressed']+=1;continue
            e.consumed=True
            value=(first.value+completed.value)/(first.volume+completed.volume)
            if s*(b.close-value)<=self.tick:
                self.stats['adverse_traded_value_not_reclaimed']+=1;continue
            entry=b.close;stop=e.low-self.tick if s>0 else e.high+self.tick
            risk=s*(entry-stop)
            objectives=[]
            for tf,book in self.books.items():
                for p in book.pivots[-24:]:
                    if p.observed_time_ns>=e.started or p.pivot_id in self.retired:continue
                    if p.side==('HIGH' if s>0 else 'LOW') and s*(p.price-entry)>self.tick:
                        objectives.append((p.price,tf))
            if not objectives or risk<=self.tick:
                self.stats['no_remaining_opposing_objective']+=1;continue
            target,tf=min(objectives,key=lambda x:s*(x[0]-entry));target-=s*self.tick
            rr=s*(target-entry)/risk
            if rr<1:
                self.stats['nearest_objective_no_room']+=1
                self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'nearest_objective_no_room',
                    'entry':entry,'stop':stop,'target':target,'rr':rr,'event':e.key})
                continue
            f=dict(source_scale=math.log(e.scale/5),source_prominence=e.prominence,
                effort_ratio=effort,extension_noise=extension/e.noise,value_reclaim=s*(entry-value)/e.unit,
                event_minutes=(b.ts-e.started)/MINUTE,first_effort=first.volume/(first.bars*e.baseline),
                response_flow=s*b.delta/max(b.volume,1e-12),risk_bps=risk/entry*10000,
                risk_range=risk/e.unit,cost_r=.0012*entry/risk,planned_rr=rr)
            self.stats['plans']+=1
            output.append(Plan(e.key+f':{b.ts}',e.key,self.symbol,Side.LONG if s>0 else Side.SHORT,
                b.ts,e.started,entry,stop,target,rr,e.level,e.scale,e.key,f'{tf}M_OPPOSING_LIQUIDITY',
                e.level-e.noise,e.level+e.noise,e.high,e.low,f,family='DEFENDED_AUCTION_PROBE'))
        return output

    def move(self):
        if len(self.history)<60:return 0.
        a=self.history[-60:];unit=max(float(np.mean([b.high-b.low for b in a])),2*self.tick)
        return (a[-1].close-a[0].open)/(unit*math.sqrt(60))

class DefendedAuctionProbe:
    def __init__(self,ticks):self.markets={s:ProbeMarket(s,t) for s,t in ticks.items()}
    def observe(self,bars):
        if len({b.ts for b in bars.values()})!=1:raise ValueError('unsynchronized observation clock')
        plans=[]
        for s,b in bars.items():plans.extend(self.markets[s].observe(b))
        if not plans:return []
        moves={s:m.move() for s,m in self.markets.items()};factor=float(np.median(list(moves.values())))
        result=[]
        for p in plans:
            f=dict(p.features);s=int(p.side.value)
            f.update(market_move=s*factor,relative_move=s*(moves[p.symbol]-factor))
            result.append(replace(p,features=f))
        return result
