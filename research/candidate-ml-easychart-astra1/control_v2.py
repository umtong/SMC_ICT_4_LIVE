"""Causal control-transfer policy, shared across the four markets.

Source: EasyChart OB pp.3-6, FVG pp.5-7, Trap pp.6-7, Channel pp.8,12.
Source-derived roles: direction/structure first; footprint refines entry;
whole origin invalidates the idea; the first opposing structure is the target.
Research translations (not claims made by the source): confirmed swing breaks,
first-return state, online impact innovations, pooled conditional outcome model.
A wick through a boundary is contextual evidence, NEVER a trade trigger alone.
"""
from __future__ import annotations
from collections import Counter, deque
from dataclasses import dataclass
import math
import numpy as np
from astra_policy import Observation, Plan, WickMap, MINUTE, SYMBOLS
from domain import Side

FEATURES = (
    'context_15','context_60','location_60','sweep_15','sweep_60',
    'impulse_range','impulse_efficiency','impulse_activity','impulse_flow',
    'opponent_reclaimed','footprint_gap','footprint_body','break_strength',
    'pullback_depth','pullback_efficiency','pullback_activity','pullback_flow',
    'pullback_duration','response_range','response_flow','response_activity',
    'innovation_fast','innovation_slow','impact_slope','market_15','market_60',
    'relative_15','relative_60','market_dispersion','risk_range','cost_r',
    'planned_rr','obstacle_distance','source_age','risk_bps','participation',
)

@dataclass
class Transfer:
    key: str
    side: int
    started: int
    created: int
    origin_index: int
    break_index: int
    level: float
    stop: float
    low: float
    high: float
    peak: float
    unit: float
    context: dict
    returned: int = -1
    finished: bool = False

class ImpactObserver:
    """Recursive least squares predicts response BEFORE incorporating its error.

    This is a signal-processing hypothesis: persistent unexplained price response
    can distinguish control from the mere presence of aggressive trading volume.
    No inventory ownership or hidden institutional intention is asserted.
    """
    def __init__(self):
        self.theta=np.zeros(3); self.p=np.eye(3)*10.
        self.fast=0.; self.slow=0.
    def observe(self,flow,activity,previous_move,move):
        x=np.array([1.,flow*math.sqrt(max(activity,.001)),previous_move])
        error=float(np.clip(move-self.theta@x,-10.,10.))
        px=self.p@x; gain=px/(.995+x@px)
        self.theta+=gain*error
        self.p=(self.p-np.outer(gain,x)@self.p)/.995
        self.fast=.9*self.fast+.1*error
        self.slow=.98*self.slow+.02*error

class ControlMarket:
    def __init__(self,symbol,tick):
        self.symbol=symbol; self.tick=tick; self.history=[]; self.five=[]
        self.books={tf:WickMap(symbol,tf,tick,pivot_spans=(2,)) for tf in (5,15,60)}
        self.agg={tf:[] for tf in self.books}; self.crossed=set(); self.touched=set()
        self.transfer=None; self.stats=Counter(); self.explanations=[]
        self.impact=ImpactObserver(); self.previous_move=0.
        self.ranges=deque(maxlen=60); self.volumes=deque(maxlen=60)
        self.current_unit=tick; self.current_volume=1.
    @staticmethod
    def aggregate(bars):
        return Observation(bars[-1].ts,bars[0].open,max(b.high for b in bars),
                           min(b.low for b in bars),bars[-1].close,
                           sum(b.volume for b in bars),sum(b.buy for b in bars),
                           sum(b.quote for b in bars),sum(b.trades for b in bars))
    def direction(self,tf):
        pivots=self.books[tf].pivots[-30:]
        hi=[p.price for p in pivots if p.side=='HIGH'][-2:]
        lo=[p.price for p in pivots if p.side=='LOW'][-2:]
        if len(hi)<2 or len(lo)<2:return 0.
        return .5*(np.sign(hi[1]-hi[0])+np.sign(lo[1]-lo[0]))
    def normalized_move(self,n):
        if len(self.history)<=n:return 0.
        a=self.history[-n:]; scale=max(sum(b.high-b.low for b in a),self.tick)*math.sqrt(n)
        return (a[-1].close-self.history[-n-1].close)*n/scale
    def _context(self,side,origin,stop,unit):
        ctx={'context_15':side*self.direction(15),'context_60':side*self.direction(60)}
        recent=self.history[-60:]; lo=min(b.low for b in recent); hi=max(b.high for b in recent)
        ctx['location_60']=side*((self.history[-1].close-lo)/max(hi-lo,self.tick)-.5)*2
        for tf in (15,60):
            level=[p for p in self.books[tf].pivots[-30:] if p.side==('LOW' if side>0 else 'HIGH') and p.observed_time_ns<origin]
            covered=[p for p in level if side*(stop-p.price)<0 and side*(self.history[-1].close-p.price)>0]
            ctx[f'sweep_{tf}']=max((min(5.,p.strength_ratio) for p in covered),default=0.)
        return ctx
    def _start_transfer(self,b):
        book=self.books[5]; i=len(self.five)-1
        if i<24:return
        prior=self.five[-2]
        for side,label in ((1,'HIGH'),(-1,'LOW')):
            pivots=[p for p in book.pivots[-20:] if p.side==label]
            if not pivots:continue
            p=pivots[-1]
            if p.pivot_id in self.crossed:continue
            if not (side*(b.close-p.price)>self.tick and side*(prior.close-p.price)<=self.tick):continue
            self.crossed.add(p.pivot_id); self.stats['control_break']+=1
            begin=p.index
            leg=self.five[begin:i+1]
            if len(leg)<2:continue
            extrema=[x.low if side>0 else -x.high for x in leg]
            j=begin+int(np.argmin(extrema)); origin=self.five[j]
            impulse=self.five[j:i+1]
            if len(impulse)<2:continue
            unit=max(float(np.mean([x.high-x.low for x in self.five[max(0,j-24):j]])),self.tick*2)
            start_index=max(0,len(self.history)-5*(i-j+1))
            stop=(min(x.low for x in impulse)-self.tick if side>0 else max(x.high for x in impulse)+self.tick)
            # The full opposing leg is the invalidation, not a convenient tiny wick.
            footprint=[x for x in impulse[:-1] if side*(x.close-x.open)<0]
            if not footprint:footprint=[origin]
            ob=footprint[-1]; zl=min(ob.open,ob.close); zh=max(ob.open,ob.close)
            is_gap=0.
            for k in range(2,len(impulse)):
                a,m,z=impulse[k-2:k+1]
                body=abs(m.close-m.open)
                if body<2*max(abs(a.close-a.open),abs(z.close-z.open),self.tick):continue
                if side>0 and z.low>a.high:
                    zl,zh=a.high,z.low; is_gap=1.
                elif side<0 and z.high<a.low:
                    zl,zh=z.high,a.low; is_gap=1.
            if zh-zl<self.tick:continue
            if side*(b.close-(zh if side>0 else zl))<=0:continue
            context=self._context(side,origin.ts,stop,unit)
            vol=sum(x.volume for x in impulse); delta=sum(x.delta for x in impulse)
            travel=sum(x.high-x.low for x in impulse)
            baseline=max(np.mean([x.volume for x in self.five[max(0,j-24):j]]),1e-12)
            # Traded-price anchor of the preceding opposing leg, observed only.
            opposing=self.five[max(0,begin-1):j+1]
            ov=sum(x.volume for x in opposing)
            ovwap=sum((x.high+x.low+x.close)/3*x.volume for x in opposing)/max(ov,1e-12)
            context.update(impulse_range=side*(b.close-origin.open)/unit,
                           impulse_efficiency=side*(b.close-origin.open)/max(travel,self.tick),
                           impulse_activity=vol/max(baseline*len(impulse),1e-12),
                           impulse_flow=side*delta/max(vol,1e-12),
                           opponent_reclaimed=side*(b.close-ovwap)/unit,
                           footprint_gap=is_gap,footprint_body=abs(ob.close-ob.open)/unit,
                           break_strength=side*(b.close-p.price)/unit,
                           source_age=(b.ts-p.observed_time_ns)/(5*MINUTE))
            self.transfer=Transfer(p.pivot_id,side,origin.ts,b.ts,start_index,len(self.history)-1,
                                   p.price,stop,zl,zh,b.high if side>0 else b.low,unit,context)
    def observe(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('nonconsecutive market observations')
        unit=max(np.mean(self.ranges) if self.ranges else b.high-b.low,self.tick*2)
        vbase=max(np.mean(self.volumes) if self.volumes else b.volume,1e-12)
        move=(b.close-(self.history[-1].close if self.history else b.open))/unit
        self.impact.observe(b.delta/max(b.volume,1e-12),b.volume/vbase,self.previous_move,move)
        self.previous_move=float(np.clip(move,-10,10)); self.current_unit=unit; self.current_volume=vbase
        self.ranges.append(b.high-b.low); self.volumes.append(b.volume); self.history.append(b)
        for tf,book in self.books.items():
            self.agg[tf].append(b)
            if b.ts//MINUTE%tf==0:
                a=self.agg[tf]
                if len(a)==tf:
                    combined=self.aggregate(a);book.observe(combined)
                    if tf==5:self.five.append(combined)
                self.agg[tf]=[]
            for p in book.pivots[-40:]:
                if p.observed_time_ns<b.ts and ((p.side=='HIGH' and b.high>=p.price) or (p.side=='LOW' and b.low<=p.price)):
                    self.touched.add(p.pivot_id)
        if b.ts//MINUTE%5==0 and len(self.five)>1:self._start_transfer(self.five[-1])
        e=self.transfer
        if e is None or e.finished or b.ts<=e.created:return None
        side=e.side
        if (side>0 and b.low<=e.stop) or (side<0 and b.high>=e.stop):
            e.finished=True; self.stats['origin_invalidated']+=1;return None
        if e.returned<0:
            e.peak=max(e.peak,b.high) if side>0 else min(e.peak,b.low)
            touches=b.low<=e.high and b.high>=e.low
            if not touches:return None
            e.returned=len(self.history)-1;self.stats['first_return']+=1
        prev=self.history[-2]
        response=(b.close>prev.high and b.close>(e.low+e.high)/2 if side>0 else b.close<prev.low and b.close<(e.low+e.high)/2)
        if not response:return None
        entry=b.close; stop=e.stop; risk=side*(entry-stop)
        levels=[(e.peak,'FIRST_IMPULSE_EXTREME')]
        for tf,book in self.books.items():
            for p in book.pivots[-40:]:
                if p.side==('HIGH' if side>0 else 'LOW') and p.pivot_id not in self.touched and side*(p.price-entry)>0:
                    levels.append((p.price,f'UNTAKEN_{tf}M_PIVOT'))
        levels=[(x,k) for x,k in levels if side*(x-entry)>0]
        if risk<=self.tick or not levels:return None
        target,kind=min(levels,key=lambda x:side*(x[0]-entry)); target-=side*self.tick
        rr=side*(target-entry)/risk
        # Do not skip a nearby obstacle to manufacture an attractive RR.
        if rr<1.:
            self.stats['first_obstacle_less_than_one_r']+=1
            self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'first_obstacle_less_than_one_r','entry':entry,'stop':stop,'target':target,'rr':rr})
            return None
        retrace=self.history[e.break_index+1:]; vol=sum(x.volume for x in retrace)
        length=sum(x.high-x.low for x in retrace)
        signed=side*sum(x.delta for x in retrace)/max(vol,1e-12)
        f=dict(e.context)
        f.update(pullback_depth=side*(e.peak-entry)/max(abs(e.peak-e.stop),self.tick),
                 pullback_efficiency=side*(entry-e.peak)/max(length,self.tick),
                 pullback_activity=vol/max(len(retrace)*self.current_volume,1e-12),
                 pullback_flow=signed,pullback_duration=(b.ts-e.created)/(5*MINUTE),
                 response_range=side*(b.close-b.open)/self.current_unit,
                 response_flow=side*b.delta/max(b.volume,1e-12),response_activity=b.volume/self.current_volume,
                 innovation_fast=side*self.impact.fast,innovation_slow=side*self.impact.slow,
                 impact_slope=float(self.impact.theta[1]),risk_range=risk/e.unit,cost_r=.0012*entry/risk,
                 planned_rr=rr,obstacle_distance=side*(target-entry)/e.unit,risk_bps=10000*risk/entry,
                 participation=(b.volume/max(b.trades,1))/max(self.current_volume/max(np.mean([x.trades for x in self.history[-60:]]),1),1e-12))
        e.finished=True;self.stats['plans']+=1
        return Plan(f'{self.symbol}:CONTROL:{e.key}:{b.ts}',f'{self.symbol}:CONTROL:{e.key}',self.symbol,
                    Side.LONG if side>0 else Side.SHORT,b.ts,e.started,entry,stop,target,rr,e.level,5,e.key,kind,
                    e.low,e.high,max(x.high for x in self.history[e.origin_index:]),min(x.low for x in self.history[e.origin_index:]),
                    f,family='CONTROL_TRANSFER_FIRST_RETURN')

class ControlPolicy:
    def __init__(self,ticks):self.markets={s:ControlMarket(s,t) for s,t in ticks.items()}
    def observe(self,bars):
        if set(bars)!=set(self.markets):raise ValueError('all four synchronized observations required')
        if len({b.ts for b in bars.values()})!=1:raise ValueError('market timestamps differ')
        plans=[p for s,b in bars.items() if (p:=self.markets[s].observe(b)) is not None]
        m15={s:m.normalized_move(15) for s,m in self.markets.items()}
        m60={s:m.normalized_move(60) for s,m in self.markets.items()}
        for p in plans:
            s=int(p.side.value); peers=[k for k in self.markets if k!=p.symbol]
            market15=float(np.median([m15[k] for k in peers]));market60=float(np.median([m60[k] for k in peers]))
            p.features.update(market_15=s*market15,market_60=s*market60,
                              relative_15=s*(m15[p.symbol]-market15),relative_60=s*(m60[p.symbol]-market60),
                              market_dispersion=float(np.std(list(m15.values()))))
        return plans
