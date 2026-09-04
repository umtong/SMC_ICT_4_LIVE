"""One causal liquidity-response policy, shared by BTC/ETH/SOL/XRP.

Source: EasyChart Fakeout/Trap pp6-7,11; OB pp4-6; FVG pp5-7;
Channel pp8,11-12. The implementation below is a research translation,
not a claim that the source specifies numeric candle windows or an ML model.
A known boundary supplies context; its breach supplies an event; recovery or
an impulsive change of control supplies direction; an origin return supplies
location. Stops are event/origin invalidations; targets are observed obstacles.
"""
from __future__ import annotations
from collections import Counter, deque
from dataclasses import dataclass
import math
import numpy as np
from astra_policy import Observation, Plan, MINUTE, SYMBOLS
from domain import Side

FEATURES = tuple(
    [f'{name}_{n}' for n in (3,15,60,240) for name in ('move','flow','efficiency','location')]
    + ['context_scale','context_age','source_strength','body','wick','activity','range_expansion','impact_residual','flow_change',
       'basis','basis_change','basis_dislocation','market_5','market_15','market_60',
       'relative_15','relative_60','context_15','context_60','channel_location',
       'source_scale','source_age','source_kind','event_age','penetration','event_flow',
       'event_activity','return_depth','return_activity','origin_overlap','entry_distance',
       'risk_range','risk_bps','cost_r','planned_rr','target_range','is_origin_return'])

@dataclass(slots=True)
class Level:
    key: str
    price: float
    kind: int                 # +1 = upper / buy-side pool, -1 = lower pool
    tf: int
    born: int
    pivot_time: int
    strength: float
    consumed: bool = False

@dataclass(slots=True)
class Challenge:
    key: str
    level: Level
    started: int
    high: float
    low: float
    volume: float
    buy: float
    baseline_volume: float
    unit: float
    target: float | None
    emitted: bool = False
    outside: int = 0

@dataclass(slots=True)
class Origin:
    key: str
    parent: Challenge
    side: int
    born: int
    low: float
    high: float
    stop: float
    departure_high: float
    departure_low: float
    destination: float | None
    overlap: int
    returned: bool = False
    return_time: int = 0
    return_volume: float = 0.
    return_count: int = 0

class Frame:
    """Five-candle wick pivots are published only after two right bars close."""
    def __init__(self, tf):
        self.tf=tf; self.rows=[]; self.bars=[]; self.levels=[]; self.pivots=[]
    def append(self, b):
        self.rows.append(b)
        if b.ts//MINUTE % self.tf: return None
        rows=self.rows; self.rows=[]
        if len(rows)!=self.tf: return None
        x=Observation(b.ts,rows[0].open,max(v.high for v in rows),min(v.low for v in rows),b.close,
                      sum(v.volume for v in rows),sum(v.buy for v in rows),sum(v.quote for v in rows),sum(v.trades for v in rows))
        self.bars.append(x)
        if len(self.bars)>=5:
            five=self.bars[-5:]; p=five[2]
            unit=max(np.median([v.high-v.low for v in self.bars[-48:]]),1e-12)
            for kind,price,others in ((1,p.high,[v.high for v in five[:2]+five[3:]]),(-1,p.low,[v.low for v in five[:2]+five[3:]])):
                if (price>max(others)) if kind>0 else (price<min(others)):
                    strength=(price-min(v.low for v in five)) / unit if kind>0 else (max(v.high for v in five)-price)/unit
                    z=Level(f'{self.tf}:{p.ts}:{kind}',price,kind,self.tf,x.ts,p.ts,float(strength))
                    self.levels.append(z); self.pivots.append(z)
            self.levels=[z for z in self.levels[-64:] if not z.consumed]
            self.pivots=self.pivots[-64:]
        return x
    def direction(self):
        h=[z for z in self.pivots if z.kind==1][-2:]
        l=[z for z in self.pivots if z.kind==-1][-2:]
        if len(h)<2 or len(l)<2: return 0.
        return float((np.sign(h[-1].price-h[-2].price)+np.sign(l[-1].price-l[-2].price))/2)
    def channel(self, ts):
        """Parallel three-wick construction; no regression-line pseudo-channel."""
        candidates=[]
        for kind in (-1,1):
            anchors=[z for z in self.pivots if z.kind==kind][-2:]
            if len(anchors)<2: continue
            a,c=anchors
            middle=[z for z in self.pivots if z.kind==-kind and a.pivot_time<z.pivot_time<c.pivot_time]
            if not middle: continue
            b=max(middle,key=lambda z:abs(z.price-a.price))
            slope=(c.price-a.price)/(c.pivot_time-a.pivot_time)
            base=c.price+slope*(ts-c.pivot_time)
            width=b.price-(a.price+slope*(b.pivot_time-a.pivot_time))
            low,high=sorted((base,base+width))
            if high>low: candidates.append((c.born,low,high,slope,c))
        return max(candidates,key=lambda x:x[0]) if candidates else None

class Market:
    def __init__(self,symbol,tick,external=None):
        self.symbol=symbol; self.tick=tick; self.external=external
        self.history=[]; self.frames={n:Frame(n) for n in (5,15,60)}
        self.pending={}; self.origins={}; self.recent=deque(maxlen=24)
        self.stats=Counter(); self.explanations=[]; self.last_ts=0
        self.bases=[]; self.last_channel_keys=set(); self.channel_levels=[]
        self.zones=[]; self.contexts={}; self.completed_reclaims=set()
    @property
    def five(self): return self.frames[5].bars
    def unit(self):
        return max(self.tick,float(np.median([b.high-b.low for b in self.five[-48:]]))) if self.five else self.tick
    def _explain(self,c,reason,ts):
        self.stats[reason]+=1
        if reason in ('plan_emitted','origin_plan_emitted'):
            self.explanations.append({'symbol':self.symbol,'ts':ts,'event':c.key,'reason':reason})
    def targets(self,side,entry,ts):
        # The first opposing structure includes the execution-scale swing and
        # opposing footprints; it is not just a remote hourly liquidity pool.
        levels=[z.price for tf in (5,15,60) for z in self.frames[tf].levels
                if not z.consumed and z.kind==side and z.born<ts and side*(z.price-entry)>self.tick]
        for z in self.zones:
            price=z['low'] if side>0 else z['high']
            if z['alive'] and z['side']==-side and z['born']<ts and side*(price-entry)>self.tick:levels.append(price)
        return levels
    def _new_zones(self,tf):
        f=self.frames[tf].bars
        if len(f)<3:return
        a,p,b=f[-3:]
        typical=max(float(np.median([abs(v.close-v.open) for v in f[-48:]])),self.tick)
        for side in (-1,1):
            engulf=(side*(p.close-p.open)<0 and side*(b.close-p.open)>0
                    and side*(b.close-b.open)>=2*abs(p.close-p.open)
                    and abs(p.close-p.open)>.1*typical)
            gap=(b.low>a.high if side>0 else b.high<a.low)
            gap=gap and side*(p.close-p.open)>=2*max(abs(a.close-a.open),abs(b.close-b.open),self.tick)
            for kind,exists in [('OB',engulf),('FVG',gap)]:
                if not exists:continue
                low,high=sorted((p.open,p.close)) if kind=='OB' else ((a.high,b.low) if side>0 else (b.high,a.low))
                bars=(p,b) if kind=='OB' else (a,p,b)
                self.zones.append(dict(key=f'{kind}:{tf}:{b.ts}:{side}',side=side,tf=tf,born=b.ts,impulse_ts=p.ts if kind=='FVG' else b.ts,low=low,high=high,
                     stop=min(v.low for v in bars)-self.tick if side>0 else max(v.high for v in bars)+self.tick,
                     extreme=b.high if side>0 else b.low,first_test=0,alive=True))
        self.zones=[z for tf in (5,15,60) for z in [v for v in self.zones if v['tf']==tf and v['alive']][-32:]]
    def _update_zones(self,b):
        for z in self.zones:
            if not z['alive'] or b.ts<=z['born']:continue
            side=z['side']
            if b.low<=z['stop'] if side>0 else b.high>=z['stop']:
                z['alive']=False;continue
            touch=b.low<=z['high'] and b.high>=z['low']
            if not z['first_test']:
                if touch:z['first_test']=b.ts
                else:z['extreme']=max(z['extreme'],b.high) if side>0 else min(z['extreme'],b.low)
            elif b.high>=z['extreme'] if side>0 else b.low<=z['extreme']:
                # The footprint's first return has completed its wave.
                z['alive']=False
    def _context(self,c):
        candidates=[z for z in self.zones if z['alive'] and z['tf']>=15 and z['born']<c.started
                    and z['side']==-c.level.kind and c.low<=z['high'] and c.high>=z['low']]
        return max(candidates,key=lambda z:(z['tf'],z['born'])) if candidates else None
    def destination(self,side,entry,ts):
        x=self.targets(side,entry,ts)
        return min(x,key=lambda p:side*(p-entry)) if x else None
    def _features(self,c,b,side,stop,target,market,origin=None):
        h=self.history; u=c.unit; out={}
        for n in (3,15,60,240):
            rows=h[-n:]; first=h[-n-1].close if len(h)>n else rows[0].open
            change=b.close-first; path=abs(rows[0].close-first)+sum(abs(y.close-x.close) for x,y in zip(rows,rows[1:]))
            volume=sum(x.volume for x in rows); delta=sum(x.delta for x in rows)
            lo=min(x.low for x in rows); hi=max(x.high for x in rows)
            out.update({f'move_{n}':side*change/u,f'flow_{n}':side*delta/max(volume,1e-12),
                        f'efficiency_{n}':side*change/max(path,self.tick),f'location_{n}':side*(2*(b.close-lo)/max(hi-lo,self.tick)-1)})
        rng=max(b.high-b.low,self.tick); previous=h[-61:-1]
        meanvol=max(float(np.mean([x.volume for x in previous])),1e-12)
        meanrng=max(float(np.mean([x.high-x.low for x in previous])),self.tick)
        flows=np.array([x.delta/max(x.volume,1e-12) for x in previous]); changes=np.array([(x.close-x.open)/u for x in previous])
        beta=float(np.dot(flows,changes)/max(np.dot(flows,flows),1e-9))
        currentflow=b.delta/max(b.volume,1e-12)
        ch=self.frames[15].channel(b.ts)
        location=side*(2*(b.close-ch[1])/max(ch[2]-ch[1],self.tick)-1) if ch else 0.
        basis=self.bases[-1] if self.bases else 0.
        bp=self.bases[-61:-1]
        bm=float(np.mean(bp)) if bp else 0.; bs=max(float(np.std(bp)),.25) if bp else .25
        risk=side*(b.close-stop)
        peak=(origin.departure_high if side>0 else origin.departure_low) if origin else (c.high if side>0 else c.low)
        depth=side*(peak-b.close)/max(abs(peak-c.level.price),self.tick)
        z=self.contexts.get(c.key)
        out.update(context_scale=math.log2(z['tf']/5) if z else 0.,
                   context_age=math.log1p((b.ts-z['born'])/(z['tf']*MINUTE)) if z else 0.,
                   source_strength=c.level.strength,
                   body=side*(b.close-b.open)/rng,wick=side*((min(b.open,b.close)-b.low)-(b.high-max(b.open,b.close)))/rng,
            activity=math.log1p(b.volume/meanvol),range_expansion=math.log1p(rng/meanrng),
            impact_residual=side*((b.close-b.open)/u-beta*currentflow),
            flow_change=side*(currentflow-float(np.mean(flows[-5:]))),
            basis=side*basis,basis_change=side*(basis-(self.bases[-6] if len(self.bases)>5 else basis)),basis_dislocation=side*(basis-bm)/bs,
            market_5=side*market.get(5,0.),market_15=side*market.get(15,0.),market_60=side*market.get(60,0.),
            relative_15=out['move_15']-side*market.get(15,0.),relative_60=out['move_60']-side*market.get(60,0.),
            context_15=side*self.frames[15].direction(),context_60=side*self.frames[60].direction(),channel_location=location,
            source_scale=math.log2(c.level.tf/5),source_age=math.log1p((c.started-c.level.pivot_time)/(c.level.tf*MINUTE)),
            source_kind=float(c.level.key.startswith('CHANNEL')),event_age=math.log1p((b.ts-c.started)/MINUTE),
            penetration=(c.level.price-c.low)/u if c.level.kind<0 else (c.high-c.level.price)/u,
            event_flow=side*(2*c.buy-c.volume)/max(c.volume,1e-12),event_activity=math.log1p(c.volume/max(c.baseline_volume,1e-12)),
            return_depth=depth,return_activity=math.log1p((origin.return_volume/max(origin.return_count,1))/meanvol) if origin else 0.,
            origin_overlap=float(origin.overlap) if origin else 0.,entry_distance=side*(b.close-c.level.price)/u,
            risk_range=risk/u,risk_bps=10000*risk/b.close,cost_r=.0006*(b.close+stop)/risk,
            planned_rr=side*(target-b.close)/risk,target_range=side*(target-b.close)/u,is_origin_return=float(origin is not None))
        return {k:float(np.clip(out[k],-100.,100.)) for k in FEATURES}
    def _plan(self,c,b,side,stop,target,market,origin=None):
        target-=side*self.tick
        risk=side*(b.close-stop); reward=side*(target-b.close)
        if risk<=self.tick or reward<risk:
            self.stats['geometry_below_one_r']+=1; return None
        features=self._features(c,b,side,stop,target,market,origin)
        return Plan(f'{self.symbol}:{c.key}:{b.ts}:{int(origin is not None)}',f'{self.symbol}:{c.key}',self.symbol,
                    Side.LONG if side>0 else Side.SHORT,b.ts,c.started,b.close,stop,target,reward/risk,
                    c.level.price,c.level.tf,c.level.key,'OBSERVED_OPPOSING_STRUCTURE',
                    origin.low if origin else c.level.price,origin.high if origin else c.level.price,
                    origin.departure_high if origin else c.high,origin.departure_low if origin else c.low,features,
                    family='LIQUIDITY_ORIGIN_RETURN' if origin else 'LIQUIDITY_RECLAIM')
    def _challenge_levels(self,b,prev):
        if len(self.five)<48: return
        sources=[z for tf in (5,15,60) for z in self.frames[tf].levels]+self.channel_levels
        hits=[]
        for z in sources:
            if z.consumed or z.born>=b.ts: continue
            hit=b.high>=z.price if z.kind>0 else b.low<=z.price
            if not hit: continue
            z.consumed=True
            if (prev.close<z.price if z.kind>0 else prev.close>z.price): hits.append(z)
        for kind in (-1,1):
            same=[z for z in hits if z.kind==kind]
            if not same: continue
            # One owner for nested pools touched by one market event.
            z=max(same,key=lambda x:(x.tf,x.strength))
            c=Challenge(f'EVENT:{b.ts}:{kind}',z,b.ts,b.high,b.low,b.volume,b.buy,
                        max(np.mean([x.volume for x in self.history[-61:-1]]),1e-12),self.unit(),self.destination(-kind,z.price,b.ts))
            z=self._context(c)
            if z is None:
                self.stats['no_prior_directional_footprint']+=1;continue
            c.key=z['key']
            c.started=z['first_test'] or c.started
            self.contexts[c.key]=z
            self.pending[kind]=c;self.recent.append(c);self.stats['boundary_challenge']+=1
    def _reclaims(self,b,prev,market):
        plans=[]
        for kind,c in list(self.pending.items()):
            if b.ts>c.started:
                c.high=max(c.high,b.high); c.low=min(c.low,b.low); c.volume+=b.volume;c.buy+=b.buy
            side=-kind
            inside=side*(b.close-c.level.price)>0
            c.outside+=int(not inside)
            # A subsequently confirmed outside swing ends the old local auction.
            newer=[z for z in self.frames[c.level.tf].pivots if z.born>c.started and z.kind==kind and kind*(z.price-c.level.price)>0]
            if newer or b.ts-c.started>4*c.level.tf*MINUTE:
                self.stats['challenge_replaced_by_new_auction']+=1;del self.pending[kind];continue
            if c.target is not None and (b.high>=c.target if side>0 else b.low<=c.target):
                self.stats['destination_already_traded']+=1;del self.pending[kind];continue
            if b.ts//MINUTE%5 or not self.five:continue
            prior=self.five[-1]
            response=(b.close>prior.high if side>0 else b.close<prior.low)
            z=self.contexts.get(c.key)
            if not z or not z['alive']:del self.pending[kind];continue
            if inside and response and not c.emitted and c.key not in self.completed_reclaims:
                c.emitted=True;self.completed_reclaims.add(c.key)
                # A five-minute transfer must invalidate at the full formation,
                # not an artificially tight one-minute noise extreme.
                c.low=min(c.low,prior.low);c.high=max(c.high,prior.high)
                if c.target is not None:
                    p=self._plan(c,b,side,c.low-self.tick if side>0 else c.high+self.tick,c.target,market)
                    if p is not None:plans.append(p);self._explain(c,'plan_emitted',b.ts)
                else:self.stats['no_observed_destination']+=1
                del self.pending[kind]
        return plans
    def _origin_returns(self,b,prev,market):
        plans=[]
        for side,o in list(self.origins.items()):
            if b.ts<=o.born:continue
            if (b.low<=o.stop if side>0 else b.high>=o.stop):
                self.stats['origin_invalidated']+=1;del self.origins[side];continue
            if o.destination is not None and (b.high>=o.destination if side>0 else b.low<=o.destination):
                self.stats['origin_destination_spent']+=1;del self.origins[side];continue
            if not o.returned:
                touch=b.low<=o.high if side>0 else b.high>=o.low
                if not touch:
                    o.departure_high=max(o.departure_high,b.high);o.departure_low=min(o.departure_low,b.low);continue
                o.returned=True;o.return_time=b.ts;self.stats['origin_first_return']+=1
            o.return_volume+=b.volume;o.return_count+=1
            response=b.close>prev.high if side>0 else b.close<prev.low
            if not response:continue
            peak=o.departure_high if side>0 else o.departure_low
            targets=[p for p in (peak,o.destination) if p is not None and side*(p-b.close)>self.tick]
            if targets:
                target=min(targets,key=lambda p:side*(p-b.close))
                p=self._plan(o.parent,b,side,o.stop,target,market,o)
                if p is not None:plans.append(p);self._explain(o.parent,'origin_plan_emitted',b.ts)
            del self.origins[side]
        return plans
    def _new_origin(self,x):
        born=[z for z in self.zones if z['tf']==5 and z['born']==x.ts]
        for side in (-1,1):
            footprints=[z for z in born if z['side']==side]
            if not footprints:continue
            candidates=[(c,z) for c in self.recent for z in footprints
                        if z['impulse_ts']>=c.started and side==-c.level.kind
                        and side*(x.close-c.level.price)>0 and c.key in self.contexts
                        and self.contexts[c.key]['alive']]
            if not candidates:continue
            c,z=max(candidates,key=lambda q:(q[0].started,q[1]['low'] if side>0 else -q[1]['high']))
            low,high=z['low'],z['high']
            stop=min(z['stop'],c.low-self.tick) if side>0 else max(z['stop'],c.high+self.tick)
            if side*(x.close-(high if side>0 else low))<=0:continue
            self.origins[side]=Origin(c.key,c,side,x.ts,low,high,stop,x.high,x.low,
                                      self.destination(side,x.close,x.ts),len(footprints))
            self.stats['liquidity_displacement_origin']+=1
    def observe(self,b,market):
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError(f'{self.symbol}: non-contiguous observation clock')
        self.last_ts=b.ts
        prev=self.history[-1] if self.history else b
        self.history.append(b)
        if self.external is not None:
            mark=self.external(self.symbol,b.ts)
            self.bases.append(10000*(b.close/mark-1))
        else:self.bases.append(0.)
        plans=[]
        self._update_zones(b)
        if len(self.history)>240:
            self._challenge_levels(b,prev)
            plans.extend(self._reclaims(b,prev,market))
            plans.extend(self._origin_returns(b,prev,market))
        for tf,frame in self.frames.items():
            x=frame.append(b)
            if x is None:continue
            self._new_zones(tf)
            if tf==5:self._new_origin(x)
            if tf in (15,60):
                ch=frame.channel(b.ts)
                if ch:
                    _,low,high,slope,anchor=ch
                    for kind,price in ((-1,low),(1,high)):
                        key=f'CHANNEL:{tf}:{anchor.key}:{kind}'
                        if key not in self.last_channel_keys:
                            self.last_channel_keys.add(key)
                            self.channel_levels.append(Level(key,price,kind,tf,b.ts,anchor.pivot_time,1.))
                    # A diagonal is evaluated at the present clock, not kept at its old price.
                    for z in self.channel_levels:
                        if z.key.startswith(f'CHANNEL:{tf}:{anchor.key}:'):
                            z.price=low if z.kind<0 else high
                self.channel_levels=[z for z in self.channel_levels if not z.consumed and b.ts-z.born<=8*z.tf*MINUTE]
        return plans

class LiquidityPolicy:
    def __init__(self,ticks,external=None):
        self.markets={s:Market(s,t,external) for s,t in ticks.items()};self.last_ts=0
    def observe(self,bars):
        if set(bars)!=set(self.markets):raise ValueError('all configured instruments must share one observation clock')
        stamps={b.ts for b in bars.values()}
        if len(stamps)!=1:raise ValueError('unequal market clocks')
        ts=stamps.pop()
        if self.last_ts and ts<=self.last_ts:raise ValueError('non-increasing policy clock')
        self.last_ts=ts;market={}
        for n in (5,15,60):
            moves=[]
            for s,b in bars.items():
                m=self.markets[s]
                if len(m.history)>=n:moves.append((b.close-m.history[-n].close)/m.unit())
            market[n]=float(np.median(moves)) if moves else 0.
        return [p for s in sorted(bars) for p in self.markets[s].observe(bars[s],market)]
