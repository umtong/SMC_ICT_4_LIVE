"""One directional auction with successive local defenses.

Source interpretation: liquidity determines direction; a *new* lower-frame
OB/FVG after a corrective wave supplies entry and its own invalidation. A new
SR-flip defense is not invalidated only at an obsolete initial trend extreme.
The 15m/60m/240m choices are research scales, not numbers prescribed by the PDFs.
"""
from dataclasses import dataclass,replace
from collections import deque
import math
import numpy as np
from policy import Market as GeometryMarket,Frame,Challenge,Origin,FEATURES as BASE
from astra_policy import MINUTE

FEATURES=BASE+('controller_age','controller_progress','controller_distance','controller_scale',
               'context_240','pullback_depth','pullback_effort','initiative_effort','defense_frame',
               'defense_body','return_effort','return_progress','peer_direction','peer_flow')

@dataclass(slots=True)
class Controller:
    parent: Challenge
    side: int
    born: int
    stop: float
    peak: float
    peak_ts: int
    origin: float
    episode: int=0
    episode_peak: float=0.
    episode_start: int=0

@dataclass(slots=True)
class Defense:
    origin: Origin
    zone: dict
    episode_start: int
    correction: list
    response: list
    returns: list
    departing_pivot: int=0

class HierarchyMarket(GeometryMarket):
    def __init__(self,symbol,tick,external=None):
        super().__init__(symbol,tick,external)
        self.frames[240]=Frame(240)
        self.attacks=deque(maxlen=32);self.controller=None;self.defense=None
        self.formed=set();self.closed_episodes=set()
    def _liquidity(self,b,prev):
        hits=[]
        for frame in self.frames.values():
            for z in frame.levels:
                if z.consumed or z.born>=b.ts:continue
                hit=b.high>=z.price if z.kind>0 else b.low<=z.price
                if not hit:continue
                z.consumed=True
                if z.tf>=60 and (prev.close<z.price if z.kind>0 else prev.close>z.price):hits.append(z)
        for c in self.attacks:
            c.high=max(c.high,b.high);c.low=min(c.low,b.low)
        if len(self.five)<48:return
        for kind in (-1,1):
            same=[z for z in hits if z.kind==kind]
            if not same:continue
            z=max(same,key=lambda v:(v.tf,v.strength))
            c=Challenge(z.key,z,b.ts,b.high,b.low,b.volume,b.buy,
                        max(np.mean([v.volume for v in self.history[-61:-1]]),1e-12),self.unit(),None)
            self.attacks.append(c);self.stats['higher_liquidity_challenge']+=1
    def _control(self,b):
        zones=[z for z in self.zones if z['tf']==15 and z['born']==b.ts]
        choices=[]
        for z in zones:
            side=z['side']
            for c in self.attacks:
                if c.started>b.ts or b.ts-c.started>c.level.tf*MINUTE:continue
                if z['impulse_ts']<c.started-15*MINUTE:continue
                if side*(b.close-c.level.price)<=0:continue
                choices.append((c,z))
        if not choices:return
        c,z=max(choices,key=lambda q:(q[0].started,q[0].level.tf))
        side=z['side']
        if self.controller is not None and self.controller.parent.key==c.key and self.controller.side==side:return
        stop=min(c.low,z['stop']) if side>0 else max(c.high,z['stop'])
        self.controller=Controller(replace(c),side,b.ts,stop,b.high if side>0 else b.low,b.ts,c.level.price)
        self.defense=None;self.stats['higher_direction_established']+=1
    def _correction(self,b):
        c=self.controller
        if c is None:return
        side=c.side
        extreme=b.high if side>0 else b.low
        if side*(extreme-c.peak)>0:
            c.peak=extreme;c.peak_ts=b.ts
            # Completing the prior initiative leg ends that corrective auction.
            # A new opposite leg, not another entry ID, creates a new episode.
            if c.episode and side*(extreme-c.episode_peak)>0:
                self._finish()
                c.episode=0;c.episode_start=0
        opposite=side*(b.close-b.open)<0
        if not c.episode and opposite:
            c.episode=c.peak_ts;c.episode_peak=c.peak;c.episode_start=b.ts-5*MINUTE
        if not c.episode:return
        key=f'{c.parent.key}:PULLBACK:{c.episode}'
        # Only a new same-direction defense created after this corrective wave
        # can locate its entry. Existing old footprints are not reinterpreted.
        fresh=[z for z in self.zones if z['tf']==5 and z['born']==b.ts and z['side']==side
               and z['impulse_ts']>c.episode_start and b.ts>c.born]
        if not fresh:return
        if key in self.closed_episodes or key in self.formed:return
        choices=[z for z in fresh if side*(b.close-(z['high'] if side>0 else z['low']))>0]
        if not choices:return
        # Closest fresh footprint, not an arbitrary preference for a deep OB.
        z=min(choices,key=lambda v:side*(b.close-(v['high'] if side>0 else v['low'])))
        correction=[v for v in self.history if c.episode_start<v.ts<=z['impulse_ts']-5*MINUTE]
        response=[v for v in self.history if z['impulse_ts']-5*MINUTE<v.ts<=b.ts]
        if not correction:return
        parent=replace(c.parent,key=key,started=c.episode_start,high=max(v.high for v in correction+response),
                       low=min(v.low for v in correction+response),volume=sum(v.volume for v in correction),buy=sum(v.buy for v in correction))
        o=Origin(key,parent,side,b.ts,z['low'],z['high'],z['stop'],b.high,b.low,None,len(fresh))
        self.defense=Defense(o,z,c.episode_start,correction,response,[])
        self.formed.add(key);self.stats['renewed_local_defense']+=1
    def _finish(self):
        if self.defense is None:return
        self.closed_episodes.add(self.defense.origin.key)
        self.defense=None
    def _returns(self,b,market):
        d=self.defense;c=self.controller
        if d is None or c is None:return []
        o=d.origin;side=o.side
        if b.ts<=o.born:return []
        frame=self.frames[5]
        after=[z for z in frame.pivots if z.pivot_time>o.born]
        peaks=[z for z in after if z.kind==side]
        if peaks:
            first=min(peaks,key=lambda z:z.pivot_time)
            turns=[z for z in after if z.kind==-side and z.pivot_time>first.pivot_time]
            if turns:
                # A confirmed corrective trough followed by a new initiative
                # wave outside the old entry zone means its first return was
                # completed elsewhere. Do not revisit it hours later.
                self.stats['first_return_completed_elsewhere']+=1
                self._finish();return []
        if b.low<=o.stop if side>0 else b.high>=o.stop:
            self.stats['new_defense_invalidated']+=1;self._finish();return []
        if not o.returned:
            touch=b.low<=o.high if side>0 else b.high>=o.low
            if not touch:
                o.departure_high=max(o.departure_high,b.high);o.departure_low=min(o.departure_low,b.low)
                return []
            o.returned=True;o.return_time=b.ts
        elif b.high>=o.departure_high if side>0 else b.low<=o.departure_low:
            self.stats['defended_wave_completed']+=1;self._finish();return []
        d.returns.append(b);o.return_volume+=b.volume;o.return_count+=1
        if not o.low<=b.close<=o.high:return []
        prior=self.history[-2]
        # Location alone is not direction. The first local response must now
        # recover the prior completed minute's extreme while still at the
        # preselected defense; do not chase when it is already beyond it.
        if not (b.close>prior.high if side>0 else b.close<prior.low):return []
        peak=o.departure_high if side>0 else o.departure_low
        targets=[z.price for tf in (15,60,240) for z in self.frames[tf].levels
                 if not z.consumed and z.kind==side and z.born<b.ts and side*(z.price-b.close)>self.tick]
        for z in self.zones:
            price=z['low'] if side>0 else z['high']
            if z['tf']>=15 and z['alive'] and z['side']==-side and side*(price-b.close)>self.tick:targets.append(price)
        if side*(peak-b.close)>self.tick:targets.append(peak)
        if not targets:return []
        target=min(targets,key=lambda v:side*(v-b.close))
        p=self._plan(o.parent,b,side,o.stop,target,market,o)
        if p is None:return []
        u=o.parent.unit;baseline=o.parent.baseline_volume
        f=dict(p.features)
        f.update(controller_age=math.log1p((b.ts-c.born)/(60*MINUTE)),
            controller_progress=side*(c.peak-c.origin)/u,controller_distance=side*(b.close-c.stop)/u,
            controller_scale=math.log2(c.parent.level.tf/5),context_240=side*self.frames[240].direction(),
            pullback_depth=side*(c.episode_peak-(min(v.low for v in d.correction) if side>0 else max(v.high for v in d.correction)))/u,
            pullback_effort=math.log1p(np.mean([v.volume for v in d.correction])/baseline),
            initiative_effort=math.log1p(np.mean([v.volume for v in d.response])/baseline),defense_frame=5.,
            defense_body=side*(d.response[-1].close-d.response[0].open)/u,
            return_effort=math.log1p(np.mean([v.volume for v in d.returns])/baseline),
            return_progress=side*(d.returns[-1].close-d.returns[0].open)/u)
        self.stats['defense_proposal']+=1
        return [replace(p,features=f,interaction_time_ns=d.episode_start,family='DIRECTED_RENEWED_DEFENSE')]
    def observe(self,b,market):
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError('non-contiguous hierarchy clock')
        self.last_ts=b.ts;prev=self.history[-1] if self.history else b;self.history.append(b)
        self.bases.append(1e4*(b.close/self.external(self.symbol,b.ts)-1) if self.external else float('nan'))
        self._update_zones(b);self._liquidity(b,prev)
        c=self.controller
        if c is not None and (b.low<=c.stop if c.side>0 else b.high>=c.stop):
            self.controller=None;self.defense=None;self.stats['higher_direction_invalidated']+=1
        plans=self._returns(b,market)
        closed={}
        for tf,frame in self.frames.items():
            x=frame.append(b)
            if x is not None:self._new_zones(tf);closed[tf]=x
        if 15 in closed:
            x=closed[15];c=self.controller
            if c is not None:
                # Breaking the latest protected swing defeats the current
                # structural thesis even before the distant initial extreme.
                protected=[z for z in self.frames[15].pivots if z.kind==-c.side and z.pivot_time>c.born]
                if protected:
                    last=max(protected,key=lambda z:z.pivot_time)
                    if c.side*(x.close-last.price)<0:
                        self.controller=None;self.defense=None
                        self.stats['current_control_defeated']+=1
            self._control(x)
        if 5 in closed:self._correction(closed[5])
        return plans

class LiquidityPolicy:
    def __init__(self,ticks,external=None,micro=None):
        self.markets={s:HierarchyMarket(s,t,external) for s,t in ticks.items()};self.last_ts=0
    def observe(self,bars):
        if set(bars)!=set(self.markets):raise ValueError('incomplete universe')
        timestamps={b.ts for b in bars.values()}
        if len(timestamps)!=1:raise ValueError('unequal universe clocks')
        ts=timestamps.pop()
        if ts<=self.last_ts:raise ValueError('non-increasing universe clock')
        self.last_ts=ts;market={}
        for n in (5,15,60):
            values=[(b.close-self.markets[s].history[-n].close)/self.markets[s].unit()
                    for s,b in bars.items() if len(self.markets[s].history)>=n]
            market[n]=float(np.median(values)) if values else 0.
        plans=[p for s in sorted(bars) for p in self.markets[s].observe(bars[s],market)]
        result=[]
        for p in plans:
            side=int(p.side.value)
            peers=[m for s,m in self.markets.items() if s!=p.symbol and len(m.history)>=16]
            f=dict(p.features)
            f.update(peer_direction=float(np.median([side*(m.history[-1].close-m.history[-16].close)/m.unit() for m in peers])),
                     peer_flow=float(np.median([side*sum(b.delta for b in m.history[-15:])/max(sum(b.volume for b in m.history[-15:]),1e-12) for m in peers])))
            result.append(replace(p,features=f))
        return result
