"""One causal policy: context -> adverse liquidity event -> initiative defeat -> first return.

An isolated wick, order block, gap, or learned chart score is not directional
permission. This policy requires a prior higher-timeframe context, an adverse
liquidity challenge, and a new displacement which defeats the opposing gap.
The overlap of the two price-delivery footprints locates the first return.

Reuses WickMap and the existing single-account Nautilus execution. Source basis:
EasyChart Fakeout/Trap section 5; OB section 4 (whole forming wick invalidation,
first wave/opposing structure objective); FVG section 5 (large center body).
The paired-initiative state and overlap are research translations, also informed
by the project's ML-MN adverse-reacceptance work. No future holding-time filter,
net-target cap, calendar filter, symbol parameters, or fitted win-rate threshold.
"""
from collections import Counter
from dataclasses import dataclass,replace
import math
import numpy as np
from astra_policy import Observation,Plan,WickMap,MINUTE
from auction_control_survival import aggregate
from domain import Side

FEATURES=('higher_direction','higher_location','source_scale','source_prominence',
 'adverse_activity','reclaim_activity','reclaim_flow','gap_overlap','defeat_distance',
 'event_age','return_age','return_flow','return_activity','risk_bps','risk_range',
 'cost_r','planned_rr','market_move','relative_move')

@dataclass
class Footprint:
    key:str;side:int;tf:int;observed:int;started:int
    low:float;high:float;stop:float;first:int
    activity:float;flow:float
    live:bool=True

@dataclass
class Challenge:
    key:str;side:int;started:int;level:float;scale:int;strength:float
    extreme:float;context_direction:bool;context_location:bool
    claimed:bool=False

@dataclass
class Reclaim:
    key:str;side:int;observed:int;started:int
    low:float;high:float;stop:float;peak:float;unit:float;features:dict
    returned:int=0;volume:float=0.;delta:float=0.;bars:int=0;consumed:bool=False

class ReclaimMarket:
    def __init__(self,symbol,tick):
        self.symbol=symbol;self.tick=tick;self.history=[]
        self.frames={tf:[] for tf in (5,15,60,240)}
        self.agg={tf:[] for tf in self.frames}
        self.books={tf:WickMap(symbol,tf,tick,pivot_spans=(2,)) for tf in self.frames}
        self.touched={};self.broken=set();self.zones=[];self.gaps=[]
        self.bias=0;self.protected=None;self.challenges={};self.reclaims={}
        self.stats=Counter();self.explanations=[]

    def _higher_direction(self,tf,b):
        if tf!=60:return
        book=self.books[60]
        for side,label in ((1,'HIGH'),(-1,'LOW')):
            levels=[p for p in book.pivots[-24:] if p.side==label and p.pivot_id not in self.broken
                    and p.observed_time_ns<b.ts and side*(b.close-p.price)>self.tick]
            if not levels:continue
            level=max(levels,key=lambda p:p.event_time_ns)
            defenses=[p for p in book.pivots[-24:] if p.side!=label and p.event_time_ns<level.observed_time_ns
                      and side*(b.close-p.price)>0]
            self.bias=side
            self.protected=max(defenses,key=lambda p:p.event_time_ns).price if defenses else None
            for p in levels:self.broken.add(p.pivot_id)
        if self.protected is not None and self.bias*(b.close-self.protected)<0:
            self.bias=0;self.protected=None

    def _footprints(self,tf):
        a=self.frames[tf]
        if len(a)<24:return
        b=a[-1];previous=a[-2];s=1 if b.close>b.open else -1
        baseline=max(float(np.mean([q.volume for q in a[-22:-2]])),1e-12)
        median_body=float(np.median([abs(q.close-q.open) for q in a[-22:-2]]))
        if tf>=60 and s*(previous.close-previous.open)<0:
            body=abs(previous.close-previous.open)
            if body>=max(2*self.tick,.25*(previous.high-previous.low)) and abs(b.close-b.open)>=2*body:
                low=min(previous.open,previous.close);high=max(previous.open,previous.close)
                stop=min(previous.low,b.low)-self.tick if s>0 else max(previous.high,b.high)+self.tick
                self.zones.append(Footprint(f'{self.symbol}:{tf}:OB:{previous.ts}',s,tf,b.ts,
                    previous.ts-(tf-1)*MINUTE,low,high,stop,len(a)-2,b.volume/baseline,
                    s*b.delta/max(b.volume,1e-12)))
        first,center,last=a[-3:]
        s=1 if center.close>center.open else -1
        body=abs(center.close-center.open)
        if body<max(median_body,2*abs(first.close-first.open),2*abs(last.close-last.open),2*self.tick):return
        low,high=(first.high,last.low) if s>0 else (last.high,first.low)
        if high-low<self.tick:return
        stop=min(q.low for q in a[-3:])-self.tick if s>0 else max(q.high for q in a[-3:])+self.tick
        gap=Footprint(f'{self.symbol}:{tf}:FVG:{center.ts}',s,tf,last.ts,first.ts-(tf-1)*MINUTE,
            low,high,stop,len(a)-3,center.volume/baseline,s*center.delta/max(center.volume,1e-12))
        if tf>=60:self.zones.append(gap)
        if tf!=5:return
        old=[q for q in self.gaps if q.side==-s and q.observed<gap.observed and q.first>=gap.first-24]
        self.gaps.append(gap);self.gaps=self.gaps[-48:]
        event=self.challenges.get(s)
        if event is None or event.claimed or event.started>last.ts:return
        if not (event.context_direction or event.context_location):return
        if s*(last.close-event.level)<=0:return
        matches=[]
        for prior in old:
            # The adverse footprint must belong to the approach into this same
            # liquidity challenge, not a gap chosen from unrelated old trading.
            if prior.observed<event.started-60*MINUTE:continue
            left,right=max(gap.low,prior.low),min(gap.high,prior.high)
            defeated=last.close>prior.high if s>0 else last.close<prior.low
            if right-left>=self.tick and defeated:matches.append((prior,left,right))
        if not matches:return
        prior,left,right=max(matches,key=lambda x:x[0].observed)
        stop=min(event.extreme,gap.stop,prior.stop)-self.tick if s>0 else max(event.extreme,gap.stop,prior.stop)+self.tick
        # prior.stop is adverse-side, so use the actual forming-candle extreme
        # of the whole price-delivery episode instead of that opposite stop.
        forming=a[prior.first:]
        stop=min(event.extreme,min(q.low for q in forming))-self.tick if s>0 else max(event.extreme,max(q.high for q in forming))+self.tick
        unit=max(float(np.mean([q.high-q.low for q in a[-22:-2]])),2*self.tick)
        features=dict(higher_direction=float(event.context_direction),higher_location=float(event.context_location),
            source_scale=math.log(event.scale/5),source_prominence=event.strength,
            adverse_activity=prior.activity,reclaim_activity=gap.activity,reclaim_flow=gap.flow,
            gap_overlap=(right-left)/unit,
            defeat_distance=s*(last.close-(prior.high if s>0 else prior.low))/unit,
            event_age=(last.ts-event.started)/(5*MINUTE))
        key=event.key+':DEFEATED:'+prior.key
        peak=max(q.high for q in forming) if s>0 else min(q.low for q in forming)
        self.reclaims[s]=Reclaim(key,s,last.ts,min(event.started,prior.started),left,right,stop,peak,unit,features)
        event.claimed=True;self.stats['opposing_initiative_defeated']+=1

    def _observe_liquidity(self,b):
        candidates={1:[],-1:[]}
        for tf in (15,60,240):
            for p in self.books[tf].pivots[-24:]:
                if p.observed_time_ns>=b.ts or p.pivot_id in self.touched:continue
                hit=b.high>p.price+self.tick if p.side=='HIGH' else b.low<p.price-self.tick
                if not hit:continue
                self.touched[p.pivot_id]=b.ts;s=-1 if p.side=='HIGH' else 1
                location=any(z.live and z.side==s and z.observed<b.ts and z.tf>=60
                    and b.low<=z.high and b.high>=z.low for z in self.zones)
                candidates[s].append((p,tf,location))
        for s,values in candidates.items():
            p,tf,location=max(values,key=lambda x:(x[1],x[0].strength_ratio))
            active=self.challenges.get(s)
            if active is not None and not active.claimed and s*(b.close-active.level)<=0:
                # A cascade consuming several levels remains one causal event.
                active.extreme=min(active.extreme,b.low) if s>0 else max(active.extreme,b.high)
                active.context_location=active.context_location or location
                if tf>active.scale:
                    active.level=p.price;active.scale=tf;active.strength=p.strength_ratio
                continue
            extreme=b.low if s>0 else b.high
            self.challenges[s]=Challenge(f'{self.symbol}:LIQUIDITY:{p.pivot_id}:{b.ts}',s,b.ts,p.price,tf,
                p.strength_ratio,extreme,self.bias==s,location)
            self.stats['higher_liquidity_challenged']+=1

    def observe(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('non-contiguous observed clock')
        previous=self.history[-1] if self.history else b
        baseline=max(float(np.mean([q.volume for q in self.history[-60:]])) if self.history else b.volume,1e-12)
        self.history.append(b)
        self._observe_liquidity(b)
        for event in self.challenges.values():
            if not event.claimed:event.extreme=min(event.extreme,b.low) if event.side>0 else max(event.extreme,b.high)
        self.zones=[z for z in self.zones if z.live]
        for z in self.zones:
            if z.side*(b.close-z.stop)<0:z.live=False
        for tf in sorted(self.frames,reverse=True):
            self.agg[tf].append(b)
            if b.ts//MINUTE%tf==0:
                seq=self.agg[tf];self.agg[tf]=[]
                if len(seq)==tf:
                    bar=aggregate(seq);self.frames[tf].append(bar)
                    self._higher_direction(tf,bar);self._footprints(tf);self.books[tf].observe(bar)
        plans=[]
        for s,r in list(self.reclaims.items()):
            if r.consumed or r.observed>=b.ts:continue
            if b.low<=r.stop if s>0 else b.high>=r.stop:
                r.consumed=True;self.stats['whole_event_invalidated']+=1;continue
            if not r.returned:
                r.peak=max(r.peak,b.high) if s>0 else min(r.peak,b.low)
                if b.low>r.high or b.high<r.low:continue
                r.returned=b.ts;self.stats['paired_zone_first_return']+=1
            r.volume+=b.volume;r.delta+=b.delta;r.bars+=1
            response=b.close>previous.high if s>0 else b.close<previous.low
            if not response or s*(b.close-(r.low+r.high)/2)<=0:continue
            r.consumed=True
            entry=b.close;risk=s*(entry-r.stop)
            objectives=[(r.peak,'FIRST_RECLAIM_WAVE')]
            opposing_inside=False
            for z in self.zones:
                if not z.live or z.side!=-s:continue
                if z.low<=entry<=z.high:opposing_inside=True
                objectives.append((z.low if s>0 else z.high,'OPPOSING_HIGHER_FOOTPRINT'))
            for tf in (15,60,240):
                for p in self.books[tf].pivots[-24:]:
                    if p.pivot_id not in self.touched and p.observed_time_ns<b.ts and p.side==('HIGH' if s>0 else 'LOW'):
                        objectives.append((p.price,f'{tf}M_OPPOSING_LIQUIDITY'))
            objectives=[(p,k) for p,k in objectives if s*(p-entry)>self.tick]
            if opposing_inside or not objectives or risk<=self.tick:
                self.stats['opposing_structure_at_entry']+=1;continue
            target,kind=min(objectives,key=lambda x:s*(x[0]-entry));target-=s*self.tick
            rr=s*(target-entry)/risk
            if rr<1:
                self.stats['first_response_no_room']+=1
                self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'first_response_no_room',
                    'entry':entry,'stop':r.stop,'target':target,'rr':rr,'event':r.key})
                continue
            f=dict(r.features);f.update(return_age=(b.ts-r.returned)/MINUTE,
                return_flow=s*r.delta/max(r.volume,1e-12),return_activity=r.volume/(r.bars*baseline),
                risk_bps=risk/entry*10000,risk_range=risk/r.unit,cost_r=.0012*entry/risk,planned_rr=rr)
            self.stats['plans']+=1
            plans.append(Plan(r.key+f':{b.ts}',r.key,self.symbol,Side.LONG if s>0 else Side.SHORT,
                b.ts,r.started,entry,r.stop,target,rr,(r.low+r.high)/2,5,r.key,kind,r.low,r.high,
                max(entry,r.peak),min(entry,r.peak),f,family='PAIRED_INITIATIVE_RECLAIM'))
        return plans

    def move(self):
        if len(self.history)<60:return 0.
        a=self.history[-60:];unit=max(float(np.mean([b.high-b.low for b in a])),2*self.tick)
        return (a[-1].close-a[0].open)/(unit*math.sqrt(60))

class PairedInitiativePolicy:
    def __init__(self,ticks):self.markets={s:ReclaimMarket(s,t) for s,t in ticks.items()}
    def observe(self,bars):
        if len({b.ts for b in bars.values()})!=1:raise ValueError('unsynchronized markets')
        plans=[]
        for s,b in bars.items():plans.extend(self.markets[s].observe(b))
        if not plans:return []
        moves={s:m.move() for s,m in self.markets.items()};factor=float(np.median(list(moves.values())))
        output=[]
        for p in plans:
            f=dict(p.features);s=int(p.side.value)
            f.update(market_move=s*factor,relative_move=s*(moves[p.symbol]-factor))
            output.append(replace(p,features=f))
        return output
