"""Liquidity first, control second, footprint entry third, first obstacle last.

This replaces the failed minor-pivot opportunity generator, not its thresholds.
A HIGH/LOW can initiate an episode ONLY on its first subsequent challenge, and
must have been observable before that challenge. Old consumed pivots never
silently become evidence of a new sweep. The opposing local pivot is frozen at
that instant, so a tiny new internal pivot cannot retrospectively redefine what
it means to overturn the move. Entry requires its actual reversal, then a return
to the footprint; a boundary wick alone does not create a trade.

Source basis: EasyChart Trap pp6-7/11, OB pp4-6, FVG pp5-7, Channel pp8/12.
Research hypothesis beyond the source: traded value of the preceding auction
is an interior destination; it may be nearer than its opposite extreme. Its
price is frozen from transactions before the challenge, not fitted to an RR.
"""
from collections import Counter,deque
from dataclasses import dataclass
import math
import numpy as np
from astra_policy import Observation,Plan,WickMap,MINUTE,SYMBOLS
from control_v2 import ImpactObserver,ControlMarket,FEATURES as OLD_FEATURES
from domain import Side

FEATURES=OLD_FEATURES+('parent_scale','parent_range','parent_age','transfer_distance',
                      'event_activity','event_flow','event_progress','auction_value_distance')

@dataclass
class PriceZone:
    key:str
    side:int
    low:float
    high:float
    invalidation:float
    observed:int
    scale:int
    live:bool=True

@dataclass
class AuctionEpisode:
    key:str
    source_kind:int
    scale:int
    level:float
    observed:int
    started:int
    start_index:int
    control_price:float
    control_key:str
    reference_value:float
    reference_other:float
    parent_age:float
    extreme:float
    finished:bool=False
    control_index:int=-1
    control_time:int=0
    stop:float=0.
    zone_low:float=0.
    zone_high:float=0.
    departure:float=0.
    returned:int=-1
    impulse_context:dict|None=None

class LiquidityMarket:
    aggregate=staticmethod(ControlMarket.aggregate)
    direction=ControlMarket.direction
    normalized_move=ControlMarket.normalized_move
    def __init__(self,symbol,tick):
        self.symbol=symbol;self.tick=tick;self.history=[];self.five=[]
        self.books={tf:WickMap(symbol,tf,tick,pivot_spans=(2,)) for tf in (5,15,60)}
        self.agg={tf:[] for tf in self.books};self.frames={tf:[] for tf in self.books}
        self.touched=set();self.zone_keys=set();self.zones=[];self.episode=None
        self.stats=Counter();self.explanations=[]
        self.ranges=deque(maxlen=60);self.volumes=deque(maxlen=60)
        self.impact=ImpactObserver();self.previous_move=0.;self.current_unit=tick;self.current_volume=1.
    def _fresh_challenge(self,b):
        if not self.history:return
        prev=self.history[-1];new=[]
        for tf,book in self.books.items():
            for p in book.pivots[-40:]:
                if p.pivot_id in self.touched or p.observed_time_ns>=b.ts:continue
                kind=1 if p.side=='HIGH' else -1
                touched=b.high>p.price if kind>0 else b.low<p.price
                if not touched:continue
                self.touched.add(p.pivot_id)
                if tf<15 or kind*(prev.close-p.price)>0:continue
                other=[q for q in book.pivots[-40:] if q.side!=p.side and q.observed_time_ns<b.ts]
                control=[q for q in self.books[5].pivots[-30:] if q.side!=p.side and q.observed_time_ns<b.ts]
                if not other or not control:continue
                start=min(p.event_time_ns,other[-1].event_time_ns)
                # Every reference transaction predates the challenge.
                prior=[x for x in self.history if start<=x.ts<b.ts]
                if not prior:continue
                v=sum(x.volume for x in prior)
                value=sum(x.quote if x.quote>0 else x.close*x.volume for x in prior)/max(v,1e-12)
                new.append(AuctionEpisode(p.pivot_id,kind,tf,p.price,p.observed_time_ns,b.ts,len(self.history),
                         control[-1].price,control[-1].pivot_id,value,other[-1].price,
                         (b.ts-p.event_time_ns)/(tf*MINUTE),b.high if kind>0 else b.low))
        if not new:return
        candidate=max(new,key=lambda e:(e.scale,-abs(e.level-prev.close)))
        old=self.episode
        if old is not None and not old.finished:
            if old.source_kind==candidate.source_kind:
                # Several nested references hit in one drive are ONE episode.
                if candidate.scale>old.scale and old.control_time==0:
                    candidate.started=old.started;candidate.start_index=old.start_index
                    candidate.extreme=max(old.extreme,candidate.extreme) if candidate.source_kind>0 else min(old.extreme,candidate.extreme)
                    self.episode=candidate
                return
            old.finished=True;self.stats['opposing_auction_superseded']+=1
        self.episode=candidate;self.stats['fresh_higher_challenge']+=1
    def _zones(self,tf):
        a=self.frames[tf]
        if len(a)<3:return
        p,b=a[-2],a[-1]
        candidates=[]
        if (p.close-p.open)*(b.close-b.open)<0:
            side=1 if b.close>b.open else -1
            prior_body=abs(p.close-p.open);body=abs(b.close-b.open)
            if prior_body>2*self.tick and body>=2*prior_body and side*(b.close-p.open)>0:
                candidates.append(('OB',side,min(p.open,p.close),max(p.open,p.close),
                                  min(p.low,b.low) if side>0 else max(p.high,b.high)))
        x,m,z=a[-3:]
        if abs(m.close-m.open)>=2*max(abs(x.close-x.open),abs(z.close-z.open),self.tick):
            if z.low>x.high:candidates.append(('FVG',1,x.high,z.low,min(x.low,m.low,z.low)))
            if z.high<x.low:candidates.append(('FVG',-1,z.high,x.low,max(x.high,m.high,z.high)))
        for kind,side,lo,hi,stop in candidates:
            key=f'{tf}:{kind}:{b.ts}:{side}'
            if key not in self.zone_keys:
                self.zone_keys.add(key);self.zones.append(PriceZone(key,side,lo,hi,stop,b.ts,tf))
        self.zones=[z for z in self.zones if z.live]
    def _update(self,b):
        unit=max(np.mean(self.ranges) if self.ranges else b.high-b.low,self.tick*2)
        vbase=max(np.mean(self.volumes) if self.volumes else b.volume,1e-12)
        move=(b.close-(self.history[-1].close if self.history else b.open))/unit
        self.impact.observe(b.delta/max(b.volume,1e-12),b.volume/vbase,self.previous_move,move)
        self.previous_move=float(np.clip(move,-10.,10.));self.current_unit=unit;self.current_volume=vbase
        self.ranges.append(b.high-b.low);self.volumes.append(b.volume);self.history.append(b)
        for z in self.zones:
            # Actual origin failure, not time-to-live or parameter expiry.
            if (z.side>0 and b.low<z.invalidation) or (z.side<0 and b.high>z.invalidation):z.live=False
        closed=[]
        for tf,book in self.books.items():
            self.agg[tf].append(b)
            if b.ts//MINUTE%tf==0:
                items=self.agg[tf];self.agg[tf]=[]
                if len(items)==tf:
                    bar=self.aggregate(items);self.frames[tf].append(bar);book.observe(bar);closed.append(tf)
                    if tf==5:self.five.append(bar)
                    self._zones(tf)
        return closed
    def _form_control(self,e,b):
        side=-e.source_kind
        if side*(b.close-e.level)<=0 or side*(b.close-e.control_price)<=self.tick:return
        # Breaking the local opposition fixed BEFORE the higher challenge.
        sequence=[x for x in self.five if x.ts>=e.started-5*MINUTE]
        if not sequence:return
        origin=min(range(len(sequence)),key=lambda j:sequence[j].low if side>0 else -sequence[j].high)
        impulse=sequence[origin:]
        footprint=[x for x in impulse[:-1] if side*(x.close-x.open)<0]
        if not footprint:
            # A turn contained in one bar can have its OB immediately before it.
            footprint=[x for x in sequence[max(0,origin-1):origin+1] if side*(x.close-x.open)<0]
        if not footprint:return
        ob=footprint[-1];lo=min(ob.open,ob.close);hi=max(ob.open,ob.close);gap=0.
        for i in range(2,len(impulse)):
            x,m,z=impulse[i-2:i+1]
            if abs(m.close-m.open)<2*max(abs(x.close-x.open),abs(z.close-z.open),self.tick):continue
            if side>0 and z.low>x.high:lo,hi,gap=x.high,z.low,1.
            if side<0 and z.high<x.low:lo,hi,gap=z.high,x.low,1.
        if hi-lo<self.tick or side*(b.close-(hi if side>0 else lo))<=0:return
        unit=max(float(np.mean([x.high-x.low for x in self.five[-25:-1]])),2*self.tick)
        event=self.history[e.start_index:];volume=sum(x.volume for x in event)
        baseline=max(float(np.mean([x.volume for x in self.five[-25:-1]])),1e-12)
        vol=sum(x.volume for x in impulse);ranges=sum(x.high-x.low for x in impulse)
        e.stop=e.extreme-side*self.tick;e.zone_low=lo;e.zone_high=hi
        e.control_time=b.ts;e.control_index=len(self.history)-1;e.departure=b.high if side>0 else b.low
        e.impulse_context={'unit':unit,'context_15':side*self.direction(15),'context_60':side*self.direction(60),
            'sweep_15':float(e.scale==15),'sweep_60':float(e.scale==60),
            'impulse_range':side*(b.close-impulse[0].open)/unit,
            'impulse_efficiency':side*(b.close-impulse[0].open)/max(ranges,self.tick),
            'impulse_activity':vol/max(len(impulse)*baseline,1e-12),
            'impulse_flow':side*sum(x.delta for x in impulse)/max(vol,1e-12),
            'opponent_reclaimed':side*(b.close-e.level)/unit,
            'footprint_gap':gap,'footprint_body':abs(ob.close-ob.open)/unit,
            'break_strength':side*(b.close-e.control_price)/unit,
            'source_age':e.parent_age,'parent_scale':math.log(e.scale/5),
            'parent_range':abs(e.reference_other-e.level)/unit,'parent_age':e.parent_age,
            'transfer_distance':side*(e.control_price-e.extreme)/unit,
            'event_activity':volume/max(len(event)*self.current_volume,1e-12),
            'event_flow':side*sum(x.delta for x in event)/max(volume,1e-12),
            'event_progress':side*(b.close-e.level)/unit}
        self.stats['opposition_overturned']+=1
    def observe(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('incomplete causal minute stream')
        self._fresh_challenge(b)
        closed=self._update(b);e=self.episode
        if e is None or e.finished:return None
        side=-e.source_kind
        if not e.control_time:
            e.extreme=max(e.extreme,b.high) if e.source_kind>0 else min(e.extreme,b.low)
            if 5 in closed and len(self.five)>=25:self._form_control(e,self.five[-1])
            return None
        if (side>0 and b.low<=e.stop) or (side<0 and b.high>=e.stop):
            e.finished=True;self.stats['control_origin_failed']+=1;return None
        if e.returned<0:
            e.departure=max(e.departure,b.high) if side>0 else min(e.departure,b.low)
            if b.low<=e.zone_high and b.high>=e.zone_low:
                e.returned=len(self.history)-1;self.stats['footprint_retested']+=1
            else:return None
        previous=self.history[-2]
        if not (b.close>previous.high and b.close>(e.zone_low+e.zone_high)/2 if side>0 else b.close<previous.low and b.close<(e.zone_low+e.zone_high)/2):return None
        entry=b.close;risk=side*(entry-e.stop)
        levels=[(e.departure,'CONTROL_DEPARTURE'),(e.reference_value,'PRIOR_AUCTION_TRADED_VALUE'),(e.reference_other,'PRIOR_AUCTION_OPPOSITE')]
        for tf,book in self.books.items():
            for p in book.pivots[-40:]:
                if p.pivot_id not in self.touched and p.side==('HIGH' if side>0 else 'LOW'):
                    levels.append((p.price,f'UNSPENT_{tf}M_SWING'))
        for z in self.zones:
            if not z.live or z.side==side:continue
            if z.low<=entry<=z.high:
                self.stats['entry_inside_opposing_zone']+=1;return None
            levels.append((z.low if side>0 else z.high,f'OPPOSING_{z.scale}M_FOOTPRINT'))
        levels=[(p,k) for p,k in levels if side*(p-entry)>self.tick]
        if risk<=self.tick or not levels:return None
        target,kind=min(levels,key=lambda x:side*(x[0]-entry));target-=side*self.tick
        rr=side*(target-entry)/risk
        if rr<1.:
            self.stats['first_obstacle_less_than_one_r']+=1
            self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'near_obstacle_not_skipped',
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
            participation=(b.volume/max(b.trades,1))/max(self.current_volume/max(np.mean([x.trades for x in local]),1),1e-12))
        e.finished=True;self.stats['plans']+=1
        region=self.history[e.start_index:]
        return Plan(f'{self.symbol}:LQC:{e.key}:{b.ts}',f'{self.symbol}:LQC:{e.key}',self.symbol,
                    Side.LONG if side>0 else Side.SHORT,b.ts,e.started,entry,e.stop,target,rr,e.level,e.scale,
                    e.key,kind,e.zone_low,e.zone_high,max(x.high for x in region),min(x.low for x in region),f,
                    family='FRESH_LIQUIDITY_CONTROL')

class LiquidityPolicy:
    def __init__(self,ticks):self.markets={s:LiquidityMarket(s,t) for s,t in ticks.items()}
    def observe(self,bars):
        if set(bars)!=set(self.markets) or len({x.ts for x in bars.values()})!=1:raise ValueError('incomplete synchronous universe')
        plans=[p for s,b in bars.items() if (p:=self.markets[s].observe(b)) is not None]
        move={n:{s:m.normalized_move(n) for s,m in self.markets.items()} for n in (15,60)}
        for p in plans:
            side=int(p.side.value);peers=[s for s in self.markets if s!=p.symbol]
            for n in (15,60):
                common=float(np.median([move[n][s] for s in peers]))
                p.features[f'market_{n}']=side*common;p.features[f'relative_{n}']=side*(move[n][p.symbol]-common)
            p.features['market_dispersion']=float(np.std(list(move[15].values())))
        return plans
