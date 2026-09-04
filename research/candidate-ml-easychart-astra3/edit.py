from pathlib import Path
import json
here=Path(__file__).resolve().parent
(here/'space_policy.py').write_text('''"""A price corridor is context, not an independent channel trading strategy.

EasyChart channel pp6-12 and Fakeout/Trap pp6-7: three known wick anchors define
where a public boundary exists. The attack's rejection/acceptance supplies the
direction. Stops invalidate that attack; the first material opposing structure
or a channel expansion supplies the pre-entry destination.
"""
from dataclasses import dataclass,replace
import math
import numpy as np
from policy import Market as GeometryMarket,Level,Challenge
from astra_policy import MINUTE
from policy import FEATURES as BASE_FEATURES

FEATURES=BASE_FEATURES+('acceptance','channel_width','channel_slope','channel_age','response_effort')

@dataclass(slots=True)
class Corridor:
    key: str
    tf: int
    born: int
    anchor: int
    low: float
    high: float
    slope: float
    third_kind: int
    first: int
    attacked: bool=False
    def at(self,ts):
        drift=self.slope*(ts-self.born)
        return self.low+drift,self.high+drift

class SpaceMarket(GeometryMarket):
    def __init__(self,symbol,tick,external=None):
        super().__init__(symbol,tick,external)
        self.corridors={};self.events={};self.models_seen=set()
    def _publish(self,tf,b):
        frame=self.frames[tf];choices=[]
        for kind in (-1,1):
            anchors=[z for z in frame.pivots if z.kind==kind][-2:]
            if len(anchors)<2:continue
            a,c=anchors
            slope=(c.price-a.price)/(c.pivot_time-a.pivot_time)
            # Main line: rising lows or falling highs. Its opposite line is
            # exactly parallel and passes through an observed intervening wick.
            if kind*slope>0:continue
            middle=[z for z in frame.pivots if z.kind==-kind and a.pivot_time<z.pivot_time<c.pivot_time]
            if not middle:continue
            middle=max(middle,key=lambda z:-kind*(z.price-a.price-slope*(z.pivot_time-a.pivot_time)))
            width=middle.price-a.price-slope*(middle.pivot_time-a.pivot_time)
            if kind*width>=0:continue
            base=a.price+slope*(b.ts-a.pivot_time)
            low,high=sorted((base,base+width))
            rows=[v for v in frame.bars if a.pivot_time<=v.ts<=b.ts]
            valid=all(v.low>=low+slope*(v.ts-b.ts)-self.tick and v.high<=high+slope*(v.ts-b.ts)+self.tick for v in rows)
            if not valid:continue
            key=f'CORRIDOR:{tf}:{a.pivot_time}:{middle.pivot_time}:{c.pivot_time}'
            if key in self.models_seen:continue
            choices.append(Corridor(key,tf,b.ts,c.pivot_time,low,high,slope,kind,a.pivot_time))
        if choices:
            model=max(choices,key=lambda z:z.anchor)
            self.models_seen.add(model.key);self.corridors[tf]=model;self.stats['causal_three_wick_corridor']+=1
    def _consume_horizontal(self,b):
        for frame in self.frames.values():
            for z in frame.levels:
                if not z.consumed and z.born<b.ts and (b.high>=z.price if z.kind>0 else b.low<=z.price):z.consumed=True
    def _attacks(self,b):
        for tf,model in self.corridors.items():
            if model.attacked or b.ts<=model.born:continue
            low,high=model.at(b.ts)
            below=b.low<low-self.tick;above=b.high>high+self.tick
            if below and above:
                model.attacked=True;self.stats['both_boundaries_in_one_bar']+=1;continue
            if not below and not above:continue
            model.attacked=True;kind=-1 if below else 1;price=low if below else high
            level=Level(model.key,price,kind,tf,model.born,model.anchor,(high-low)/self.unit())
            c=Challenge(model.key,level,b.ts,b.high,b.low,b.volume,b.buy,
                        max(np.mean([v.volume for v in self.history[-61:-1]]),1e-12),self.unit(),None)
            self.events[tf]=(model,c);self.stats['corridor_liquidity_attack']+=1
    def _destination(self,model,side,entry,ts,accepted):
        low,high=model.at(ts);width=high-low
        native=(high if side>0 else low) if not accepted else ((high if side>0 else low)+side*width/2)
        levels=[z.price for tf in (5,15,60) if tf>=model.tf for z in self.frames[tf].levels
                if not z.consumed and z.kind==side and z.born<ts and side*(z.price-entry)>self.tick]
        for z in self.zones:
            if z['tf']<model.tf or not z['alive'] or z['side']!=-side:continue
            price=z['low'] if side>0 else z['high']
            if side*(price-entry)>self.tick:levels.append(price)
        if side*(native-entry)>self.tick:levels.append(native)
        return min(levels,key=lambda v:side*(v-entry)) if levels else None
    def _decide(self,b,prev,market):
        out=[]
        for tf,(model,c) in list(self.events.items()):
            if b.ts>c.started:
                c.high=max(c.high,b.high);c.low=min(c.low,b.low);c.volume+=b.volume;c.buy+=b.buy
            low,high=model.at(b.ts);kind=c.level.kind;c.level.price=low if kind<0 else high
            inside=kind*(b.close-c.level.price)<0
            side=-kind if inside else kind;accepted=not inside
            if inside:
                if side*(b.close-prev.close)<=0:continue
                if not low<b.close<high:
                    del self.events[tf];continue
            else:
                closed=self.frames[tf].bars[-1]
                prior_low,prior_high=model.at(closed.ts)
                prior_level=prior_low if kind<0 else prior_high
                # The next source-timeframe candle has actually opened outside.
                if not (b.ts//MINUTE%tf==1 and closed.ts>=c.started and kind*(closed.close-prior_level)>0
                        and kind*(b.open-c.level.price)>0):continue
            stop=c.low-self.tick if side>0 else c.high+self.tick
            target=self._destination(model,side,b.close,b.ts,accepted)
            if target is not None:
                p=self._plan(c,b,side,stop,target,market)
                if p is not None:
                    f=dict(p.features)
                    f.update(acceptance=float(accepted),channel_width=(high-low)/c.unit,
                        channel_slope=side*model.slope*tf*MINUTE/c.unit,
                        channel_age=(b.ts-model.born)/(tf*MINUTE),
                        channel_location=side*(2*(b.close-low)/(high-low)-1),
                        response_effort=side*b.delta/max(b.volume,1e-12))
                    out.append(replace(p,features=f,interaction_time_ns=c.started,
                               family='CORRIDOR_ACCEPTANCE' if accepted else 'CORRIDOR_REJECTION'))
                    self.stats['corridor_plan']+=1
                    del self.events[tf]
            # Geometry may become actionable after a fresh test, but the same
            # actual attack remains the causal identity until a plan is emitted.
        return out
    def observe(self,b,market):
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError('non-contiguous market')
        self.last_ts=b.ts;prev=self.history[-1] if self.history else b;self.history.append(b)
        self.bases.append(1e4*(b.close/self.external(self.symbol,b.ts)-1) if self.external else float('nan'))
        self._update_zones(b);self._consume_horizontal(b)
        if len(self.five)>=48:self._attacks(b)
        plans=self._decide(b,prev,market) if len(self.five)>=48 else []
        for tf,frame in self.frames.items():
            x=frame.append(b)
            if x is not None:self._new_zones(tf);self._publish(tf,x)
        return plans

class LiquidityPolicy:
    def __init__(self,ticks,external=None,micro=None):
        self.markets={s:SpaceMarket(s,t,external) for s,t in ticks.items()};self.last_ts=0
    def observe(self,bars):
        if set(bars)!=set(self.markets):raise ValueError('incomplete universe')
        timestamps={b.ts for b in bars.values()}
        if len(timestamps)!=1:raise ValueError('unequal clocks')
        ts=timestamps.pop()
        if ts<=self.last_ts:raise ValueError('non-increasing clock')
        self.last_ts=ts;market={}
        for n in (5,15,60):
            values=[(b.close-self.markets[s].history[-n].close)/self.markets[s].unit()
                    for s,b in bars.items() if len(self.markets[s].history)>=n]
            market[n]=float(np.median(values)) if values else 0.
        return [p for s in sorted(bars) for p in self.markets[s].observe(bars[s],market)]
''')
p=here/'research.py';s=p.read_text()
s=s.replace('from flow_policy import LiquidityPolicy,FEATURES as AUCTION_FEATURES','from space_policy import LiquidityPolicy,FEATURES as AUCTION_FEATURES')
s=s.replace("('policy.py','auction_policy.py','flow_policy.py','executed_flow.py')","('policy.py','space_policy.py')")
s=s.replace('            if micro is None:continue','            if micro is None:micro={k:float("nan") for k in MICRO_FEATURES}')
p.write_text(s)
features=['move_15','move_60','move_240','flow_15','flow_60','efficiency_15','efficiency_60','location_60',
          'body','wick','range_expansion','context_15','context_60','cost_r','planned_rr','risk_bps',
          'source_scale','source_strength','event_age','entry_distance','penetration','event_flow','event_activity',
          'acceptance','channel_width','channel_slope','channel_age','channel_location','response_effort',
          'market_15','market_60','relative_15','x_spot_flow_15','x_relative_move_15','x_oi_change_15','x_premium']
r={'months':['2024-03','2024-08'],'train_end':'2024-08-11','calibration_end':'2024-08-15','features':features,
   'experiments':[{'name':'v9_corridor_raw_aug16_24','month':'2024-08','start':'2024-08-16','end':'2024-08-24','raw':True},
                  {'name':'v9_corridor_learned_aug16_24','month':'2024-08','start':'2024-08-16','end':'2024-08-24'}]}
(here/'request.json').write_text(json.dumps(r,indent=2)+'\n')
Path(__file__).unlink()
