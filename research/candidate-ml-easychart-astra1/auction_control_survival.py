"""A single directional auction-control policy, not a collection of entry patterns.

Prior experiments largely sold every high challenge and bought every low
challenge. This policy does neither. An auction owns direction only after its
impulse defeats pre-existing opposing structure. Its originating inventory zone
remains the invalidation point. A later correction is an entry opportunity only
when price reclaims the adverse correction's traded value at that origin.

EasyChart translations: OB body after a >=2-body displacement; wick-anchored
structure; a fully specified first return with the nearest opposing objective.
The protected-origin control graph and adverse-volume value are research
hypotheses, not claims about identifiable institutions or actual inventory.
"""
from collections import Counter, deque
from dataclasses import dataclass, replace
import math
import numpy as np
from astra_policy import Observation, Plan, WickMap, MINUTE
from domain import Side

FEATURES = ('control_scale','defeated_scale','impulse_efficiency','impulse_activity',
            'impulse_flow','break_distance','control_age','pullback_depth',
            'pullback_activity','pullback_flow','opponent_value_reclaim',
            'response_flow','response_activity','risk_bps','risk_range','cost_r',
            'planned_rr','market_move','relative_move')

@dataclass
class Origin:
    key: str
    side: int
    scale: int
    observed: int
    started: int
    low: float
    high: float
    stop: float
    extreme: float
    unit: float
    volume: float
    features: dict
    live: bool = True
    controls: bool = False
    returned: int = 0
    consumed: bool = False
    adverse_volume: float = 0.
    adverse_value: float = 0.
    return_volume: float = 0.
    return_delta: float = 0.
    return_bars: int = 0


def aggregate(items):
    return Observation(items[-1].ts,items[0].open,max(b.high for b in items),
        min(b.low for b in items),items[-1].close,sum(b.volume for b in items),
        sum(b.buy for b in items),sum(b.quote for b in items),sum(b.trades for b in items))


class AuctionMarket:
    def __init__(self,symbol,tick):
        self.symbol=symbol;self.tick=tick;self.history=[]
        self.frames={tf:[] for tf in (5,15,60,240)}
        self.agg={tf:[] for tf in self.frames}
        self.books={tf:WickMap(symbol,tf,tick,pivot_spans=(2,)) for tf in self.frames}
        self.origins=[];self.control=None;self.consumed_pivots=set()
        self.stats=Counter();self.explanations=[]
        self.ranges=deque(maxlen=60);self.volumes=deque(maxlen=60)
        self.last_opposing_defeat={1:0,-1:0}

    def _form_origin(self,tf):
        a=self.frames[tf]
        if len(a)<10:return
        b=a[-1];s=1 if b.close>b.open else -1
        choices=[j for j in range(max(0,len(a)-4),len(a)-1) if s*(a[j].close-a[j].open)<0]
        if not choices:return
        j=choices[-1];ob=a[j];impulse=a[j+1:]
        body=abs(ob.close-ob.open)
        if body<2*self.tick or s*(b.close-ob.open)<2*body:return
        # An impulse must actually leave its entire originating wick range.
        if s*(b.close-(ob.high if s>0 else ob.low))<=0:return
        start=ob.ts-(tf-1)*MINUTE
        existing=[z for z in self.origins if z.side==-s and z.observed<start]
        defeated=[z for z in existing if s*(b.close-z.stop)>self.tick
                  and s*(ob.open-z.stop)<=0 and z.scale>=15]
        pivots=[(q,t) for t,book in self.books.items() if t>=15
                for q in book.pivots[-20:] if q.observed_time_ns<start
                and q.pivot_id not in self.consumed_pivots
                and q.side==('HIGH' if s>0 else 'LOW')
                and s*(b.close-q.price)>self.tick and s*(ob.open-q.price)<=0]
        level=max([z.scale for z in defeated]+[t for _,t in pivots]+[0])
        controls=tf>=15 and level>=15
        unit=max(float(np.mean([x.high-x.low for x in a[max(0,j-20):j]])),self.tick*2)
        baseline=max(float(np.mean([x.volume for x in a[max(0,j-20):j]])),1e-12)
        v=sum(x.volume for x in impulse)
        stop=(min(x.low for x in a[j:])-self.tick if s>0 else max(x.high for x in a[j:])+self.tick)
        crossed=[z.stop for z in defeated]+[q.price for q,_ in pivots]
        f={'control_scale':math.log(tf/5),'defeated_scale':math.log(max(level,5)/5),
           'impulse_efficiency':s*(b.close-ob.open)/max(sum(x.high-x.low for x in a[j:]),self.tick),
           'impulse_activity':v/max(len(impulse)*baseline,1e-12),
           'impulse_flow':s*sum(x.delta for x in impulse)/max(v,1e-12),
           'break_distance':min([s*(b.close-p)/unit for p in crossed]+[0.])}
        z=Origin(f'{self.symbol}:{tf}:ORIGIN:{ob.ts}:{s}',s,tf,b.ts,start,
                 min(ob.open,ob.close),max(ob.open,ob.close),stop,
                 max(x.high for x in a[j:]) if s>0 else min(x.low for x in a[j:]),unit,baseline,f,controls=controls)
        if any(x.key==z.key for x in self.origins):return
        self.origins.append(z);self.stats['origin_created']+=1
        for q,_ in pivots:self.consumed_pivots.add(q.pivot_id)
        if controls:
            # Overlapping same-direction footprints describe the same drive.
            # They may strengthen its authority but do not create another entry.
            old=self.control
            if old is not None and old.live and old.side==s and z.started<=old.observed:
                self.stats['same_drive_not_split']+=1
                return
            if old is not None and old.live and old.side==-s and s*(b.close-old.stop)<=0:
                self.stats['opposite_control_not_defeated']+=1
                return
            self.control=z;self.stats['direction_control_transferred']+=1

    def move(self,n=60):
        if len(self.history)<n:return 0.
        a=self.history[-n:];unit=max(float(np.mean([b.high-b.low for b in a])),self.tick*2)
        return (a[-1].close-a[0].open)/(unit*math.sqrt(n))

    def observe(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('missing observed minute')
        prev=self.history[-1] if self.history else b
        vbase=max(float(np.mean(self.volumes)) if self.volumes else b.volume,1e-12)
        self.history.append(b);self.ranges.append(b.high-b.low);self.volumes.append(b.volume)
        # Retain invalidated origins long enough to recognize the impulse that
        # defeated them. Direction survives wicks, but trade stops never do.
        for z in self.origins:
            if z.live and z.side*(b.close-z.stop)<0:z.live=False
        for tf in sorted(self.frames,reverse=True):
            self.agg[tf].append(b)
            if b.ts//MINUTE%tf==0:
                seq=self.agg[tf];self.agg[tf]=[]
                if len(seq)==tf:
                    bar=aggregate(seq);self.frames[tf].append(bar)
                    self._form_origin(tf);self.books[tf].observe(bar)
        self.origins=[z for z in self.origins if z.live or b.ts-z.observed<240*MINUTE]
        z=self.control
        if z is None or not z.live or z.consumed or z.observed>=b.ts:return None
        s=z.side
        if (b.low<=z.stop if s>0 else b.high>=z.stop):
            z.consumed=True;self.stats['protected_origin_wick_failed']+=1;return None
        if not z.returned:
            z.extreme=max(z.extreme,b.high) if s>0 else min(z.extreme,b.low)
            if b.low>z.high or b.high<z.low:return None
            z.returned=b.ts;self.stats['origin_first_return']+=1
        z.return_volume+=b.volume;z.return_delta+=b.delta;z.return_bars+=1
        adverse=max(-s*b.delta,0.)
        value=b.quote/b.volume if b.quote>0 and b.volume>0 else (b.high+b.low+b.close)/3
        z.adverse_volume+=adverse;z.adverse_value+=adverse*value
        if not z.adverse_volume:return None
        rival_value=z.adverse_value/z.adverse_volume
        if s*(b.close-rival_value)<=self.tick:return None
        if not (b.close>prev.high if s>0 else b.close<prev.low):return None
        entry=b.close;distance=s*(entry-z.stop)
        objectives=[(z.extreme,'IMPULSE_EXTERNAL_LIQUIDITY')]
        for other in self.origins:
            if other.live and other.side==-s and other.scale>=15:
                if other.low<=entry<=other.high:
                    self.stats['inside_opposing_inventory']+=1;return None
                objectives.append((other.low if s>0 else other.high,'OPPOSING_ORIGIN'))
        for tf,book in self.books.items():
            if tf<15:continue
            for q in book.pivots[-20:]:
                if q.observed_time_ns<b.ts and q.pivot_id not in self.consumed_pivots and q.side==('HIGH' if s>0 else 'LOW'):
                    objectives.append((q.price,f'{tf}M_OPPOSING_LIQUIDITY'))
        objectives=[(p,k) for p,k in objectives if s*(p-entry)>self.tick]
        if not objectives or distance<=self.tick:return None
        target,kind=min(objectives,key=lambda x:s*(x[0]-entry));target-=s*self.tick
        rr=s*(target-entry)/distance
        if rr<1:
            self.stats['nearest_objective_inadequate']+=1
            self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'nearest_objective_inadequate',
                'entry':entry,'stop':z.stop,'target':target,'rr':rr,'control':z.key})
            return None
        f=dict(z.features)
        f.update(control_age=(b.ts-z.observed)/(z.scale*MINUTE),
            pullback_depth=s*(z.extreme-entry)/max(abs(z.extreme-z.stop),self.tick),
            pullback_activity=z.return_volume/max(z.return_bars*vbase,1e-12),
            pullback_flow=s*z.return_delta/max(z.return_volume,1e-12),
            opponent_value_reclaim=s*(entry-rival_value)/z.unit,
            response_flow=s*b.delta/max(b.volume,1e-12),response_activity=b.volume/vbase,
            risk_bps=10000*distance/entry,risk_range=distance/z.unit,cost_r=.0012*entry/distance,planned_rr=rr)
        z.consumed=True;self.stats['plans']+=1
        return Plan(z.key+':'+str(b.ts),z.key,self.symbol,Side.LONG if s>0 else Side.SHORT,
            b.ts,z.returned,entry,z.stop,target,rr,(z.low+z.high)/2,z.scale,z.key,kind,z.low,z.high,
            max(entry,z.extreme),min(entry,z.extreme),f,family='AUCTION_CONTROL_SURVIVAL')


class AuctionControlPolicy:
    def __init__(self,ticks):self.markets={s:AuctionMarket(s,t) for s,t in ticks.items()}
    def observe(self,bars):
        if len({b.ts for b in bars.values()})!=1:raise ValueError('unsynchronized observations')
        candidates=[p for s,b in bars.items() if (p:=self.markets[s].observe(b)) is not None]
        moves={s:m.move() for s,m in self.markets.items()};factor=float(np.median(list(moves.values())))
        result=[]
        for p in candidates:
            f=dict(p.features);side=int(p.side.value)
            f.update(market_move=side*factor,relative_move=side*(moves[p.symbol]-factor))
            result.append(replace(p,features=f))
        return result
