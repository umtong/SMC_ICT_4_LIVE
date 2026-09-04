"""Direction from a liquidity attack's price response; location from its inventory.

This is a research generalization of EasyChart's liquidity/structure logic, not
an assertion that its PDFs prescribe VWAP or observed 5-second trade markouts.
The numerical OB/FVG body-ratio gate is deliberately not retained: it did not
establish a cost-paying direction in the preceding short experiments.
"""
from collections import deque
from dataclasses import dataclass,replace
import math
import numpy as np
from policy import Market as GeometryMarket,Challenge,Origin
from auction_policy import FEATURES as OLD_FEATURES
from astra_policy import MINUTE

FEATURES=OLD_FEATURES+('peer_direction','peer_flow','peer_response','relative_displacement')

@dataclass(slots=True)
class Attack:
    event: Challenge
    buy: float=0.
    sell: float=0.
    buy_quote: float=0.
    sell_quote: float=0.
    rows: object=None

@dataclass(slots=True)
class Control:
    origin: Origin
    acceptance: bool
    attack: object
    response: object
    returns: object

class FlowMarket(GeometryMarket):
    def __init__(self,symbol,tick,external,micro):
        super().__init__(symbol,tick,external)
        self.micro=micro;self.attacks={};self.controls={};self.latest_flow=None
    @staticmethod
    def flow(rows,side):
        return side*sum(v.delta for v in rows)/max(sum(v.volume for v in rows),1e-12)
    def _sources(self,b,prev):
        hits=[]
        for frame in self.frames.values():
            for z in frame.levels:
                if z.consumed or z.born>=b.ts:continue
                hit=b.high>=z.price if z.kind>0 else b.low<=z.price
                if not hit:continue
                z.consumed=True
                if z.tf>=15 and (prev.close<z.price if z.kind>0 else prev.close>z.price):hits.append(z)
        if len(self.five)<48:return
        for kind in (-1,1):
            matching=[z for z in hits if z.kind==kind]
            if not matching:continue
            z=max(matching,key=lambda v:(v.tf,v.strength))
            c=Challenge(z.key,z,b.ts,b.high,b.low,0.,0.,
                        max(np.mean([v.volume for v in self.history[-61:-1]]),1e-12),self.unit(),None)
            self.attacks[kind]=Attack(c,rows=[])
            self.stats['meaningful_liquidity_attack']+=1
    def _inventory_response(self,b):
        raw=self.micro.raw_at(self.symbol,b.ts)
        if raw is None:
            self.attacks.clear();self.controls.clear();return
        for kind,a in list(self.attacks.items()):
            c=a.event;c.high=max(c.high,b.high);c.low=min(c.low,b.low)
            a.buy+=float(raw.buy_volume);a.sell+=float(raw.volume-raw.buy_volume)
            a.buy_quote+=float(raw.buy_quote_volume);a.sell_quote+=float(raw.quote_volume-raw.buy_quote_volume)
            a.rows.append(b);c.volume=a.buy+a.sell;c.buy=a.buy
            side=-kind if kind*(b.close-c.level.price)<0 else kind
            acceptance=side==kind
            amount=a.buy if kind>0 else a.sell
            if amount<=0:continue
            basis=(a.buy_quote if kind>0 else a.sell_quote)/amount
            m=self.micro.at(self.symbol,b.ts,side,1e4*c.unit/b.close)
            if m is None:continue
            if side*(b.close-basis)<=0:continue
            if acceptance:
                if not self.five:continue
                closed=self.five[-1]
                accepted=(closed.ts>c.started and side*(closed.close-c.level.price)>0
                          and side*(b.open-c.level.price)>0 and m['m_own_markout_5']>0)
                if not accepted:continue
            else:
                # Aggression at the breached boundary is now losing money.
                # This is an observed adverse response, not a wick-size proxy.
                if m['m_opponent_markout_5']<=0:continue
            low,high=sorted((basis,c.level.price))
            if high-low<self.tick:continue
            if side*(b.close-(high if side>0 else low))<=0:continue
            prior=self.five[-1]
            stop=min(c.low,prior.low)-self.tick if side>0 else max(c.high,prior.high)+self.tick
            original=Origin(c.key,c,side,b.ts,low,high,stop,b.high,b.low,None,1)
            self.controls[side]=Control(original,acceptance,list(a.rows),[b],[])
            self.stats['accepted_inventory_control' if acceptance else 'rejected_inventory_control']+=1
            del self.attacks[kind]
    def _retest(self,b,market):
        out=[]
        for side,control in list(self.controls.items()):
            o=control.origin
            if b.ts<=o.born:continue
            if b.low<=o.stop if side>0 else b.high>=o.stop:
                self.stats['inventory_control_invalidated']+=1;del self.controls[side];continue
            if not o.returned:
                touch=b.low<=o.high if side>0 else b.high>=o.low
                if not touch:
                    o.departure_high=max(o.departure_high,b.high);o.departure_low=min(o.departure_low,b.low)
                    control.response.append(b);continue
                o.returned=True;o.return_time=b.ts;self.stats['inventory_first_retest']+=1
            elif b.high>=o.departure_high if side>0 else b.low<=o.departure_low:
                self.stats['inventory_wave_already_completed']+=1;del self.controls[side];continue
            control.returns.append(b);o.return_volume+=b.volume;o.return_count+=1
            if not o.low<=b.close<=o.high:continue
            m=self.micro.at(self.symbol,b.ts,side,1e4*o.parent.unit/b.close)
            if m is None:continue
            # The return must actually fail to advance against the defended
            # inventory: opposing trades have adverse markouts and late price
            # progression turns back toward the original directional thesis.
            if not (m['m_opponent_markout_1']>0 and m['m_late_progress_1']>0):continue
            peak=o.departure_high if side>0 else o.departure_low
            ahead=[v for v in self.targets(side,b.close,b.ts)+[peak] if side*(v-b.close)>self.tick]
            if not ahead:continue
            target=min(ahead,key=lambda v:side*(v-b.close))
            p=self._plan(o.parent,b,side,o.stop,target,market,o)
            if p is None:continue
            a=control.attack;r=control.response;rt=control.returns;u=o.parent.unit
            f=dict(p.features);f.update(m)
            f.update(acceptance=float(control.acceptance),control_frame=math.log2(o.parent.level.tf/5),
                attack_flow=self.flow(a,side),attack_progress=side*(a[-1].close-a[0].open)/u,
                attack_activity=math.log1p(sum(v.volume for v in a)/(len(a)*o.parent.baseline_volume)),
                response_progress=side*(r[-1].close-r[0].open)/u,response_flow=self.flow(r,side),
                response_activity=math.log1p(sum(v.volume for v in r)/(len(r)*o.parent.baseline_volume)),
                retracement_flow=self.flow(rt,side),retracement_efficiency=side*(rt[-1].close-rt[0].open)/max(sum(abs(v.close-v.open) for v in rt),self.tick),
                liquidity_origin_overlap=1.)
            out.append(replace(p,features=f,interaction_time_ns=o.parent.started,
                       family='INVENTORY_ACCEPTANCE' if control.acceptance else 'INVENTORY_REJECTION'))
            self.stats['inventory_plan']+=1;del self.controls[side]
        return out
    def observe(self,b,market):
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError('non-contiguous market clock')
        self.last_ts=b.ts;prev=self.history[-1] if self.history else b
        self.history.append(b)
        self.bases.append(1e4*(b.close/self.external(self.symbol,b.ts)-1) if self.external else float('nan'))
        self._update_zones(b)
        self._sources(b,prev)
        plans=self._retest(b,market) if len(self.five)>=48 else []
        if len(self.five)>=48:self._inventory_response(b)
        for tf,frame in self.frames.items():
            x=frame.append(b)
            if x is not None:self._new_zones(tf)
        return plans

class LiquidityPolicy:
    def __init__(self,ticks,external,micro):
        self.markets={s:FlowMarket(s,t,external,micro) for s,t in ticks.items()};self.micro=micro;self.last_ts=0
    def observe(self,bars):
        if set(bars)!=set(self.markets):raise ValueError('incomplete universe')
        timestamps={b.ts for b in bars.values()}
        if len(timestamps)!=1:raise ValueError('unsynchronized universe')
        ts=timestamps.pop()
        if ts<=self.last_ts:raise ValueError('non-increasing clock')
        self.last_ts=ts;market={}
        for n in (5,15,60):
            values=[(b.close-self.markets[s].history[-n].close)/self.markets[s].unit()
                    for s,b in bars.items() if len(self.markets[s].history)>=n]
            market[n]=float(np.median(values)) if values else 0.
        plans=[p for s in sorted(bars) for p in self.markets[s].observe(bars[s],market)]
        out=[]
        for p in plans:
            side=int(p.side.value);moves=[];flows=[];responses=[]
            for s,m in self.markets.items():
                if s==p.symbol or len(m.history)<16:continue
                rows=m.history[-15:]
                moves.append(side*(rows[-1].close-m.history[-16].close)/m.unit())
                flows.append(m.flow(rows,side))
                flow=self.micro.at(s,ts,side,1e4*m.unit()/rows[-1].close)
                if flow is not None:responses.append(flow['m_own_markout_5'])
            f=dict(p.features)
            f.update(peer_direction=float(np.median(moves)),peer_flow=float(np.median(flows)),
                     peer_response=float(np.median(responses)) if responses else float('nan'),
                     relative_displacement=f['move_15']-float(np.median(moves)))
            out.append(replace(p,features=f))
        return out
