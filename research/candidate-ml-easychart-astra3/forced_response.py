"""Forced supply at a public boundary, then an observable response.

EasyChart supplies source/response/invalidation/objective relationships. Actual
forced-order broadcasts supply a new economic observation. Quote-depth change
is an optional participation observation, not identified institutional intent.

Research assumptions: five-minute forcing above its previous day's 98th
percentile; a closed opposite-body response supplies entry. The target is fixed
at episode birth: the first boundary/value/structure return. No RR cap, hold
limit, daily limit, partial order, or moved stop is introduced.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import Counter,deque
import math
import numpy as np
from astra_policy import Observation,Plan,MINUTE
from policy import Frame
from domain import Side

FEATURES=('forcing_strength','forcing_share','reported_pressure','response_body',
          'response_progress','recovery_fraction','defending_depth_change',
          'relative_depth_change','depth_skew','common_response','context_direction',
          'source_scale','source_distance','elapsed_episode','risk_range','cost_r','planned_rr')

@dataclass
class ForcedEpisode:
    key:str
    side:int
    started:int
    detected:int
    source:float
    source_id:str
    scale:int
    extreme:float
    target:float
    unit:float
    initial_forcing:float
    initial_bid:float
    initial_ask:float
    emitted:bool=False

class ForcedMarket:
    def __init__(self,symbol,tick,selection='response'):
        self.symbol=symbol;self.tick=tick;self.selection=selection
        self.history=deque(maxlen=1441);self.frames={n:Frame(n) for n in (5,15,60)}
        self.touches={};self.active=None;self.stats=Counter();self.explanations=[]
        self.last_ts=0;self.books=deque(maxlen=6)

    def unit(self):
        bars=self.frames[5].bars[-48:]
        return max(self.tick,float(np.median([v.high-v.low for v in bars]))) if bars else self.tick

    def seed(self,b,evidence):
        history=list(self.history)
        if len(history)<241 or not evidence['ready']:return
        choices=[]
        for direction,name in ((1,'buy'),(-1,'sell')):
            force=evidence[f'{name}_5'];threshold=evidence[f'{name}_threshold']
            if not math.isfinite(threshold) or force<=0 or force<=threshold:continue
            choices.append((force,direction,max(threshold,1e-12)))
        if not choices:return
        force,direction,threshold=max(choices)
        side=-direction;start=history[-6].ts
        sources=[]
        for tf in (15,60):
            for z in self.frames[tf].pivots:
                touched=self.touches.get(z.key,0)
                if z.kind==direction and z.born<start and start<touched<=b.ts:
                    sources.append((tf,touched,z.price,z.key))
        prior=history[-65:-5]
        level=max(v.high for v in prior) if direction>0 else min(v.low for v in prior)
        crossing=next((v.ts for v in history[-5:] if (v.high>level if direction>0 else v.low<level)),None)
        if crossing is not None:sources.append((60,crossing,level,f'PRE_HOUR:{start}:{direction}'))
        if not sources:return
        tf,touched,source,key=max(sources)
        rows=history[-5:]
        extreme=max(v.high for v in rows) if direction>0 else min(v.low for v in rows)
        volume=sum(v.volume for v in history[-21:-6])
        if volume<=0:return
        value=sum(v.quote for v in history[-21:-6])/volume
        objectives=[source,value]
        for frame in self.frames.values():
            objectives += [z.price for z in frame.pivots if z.kind==side and z.born<start and z.key not in self.touches]
        ahead=[v for v in objectives if side*(v-b.close)>self.tick]
        if not ahead:return
        target=min(ahead,key=lambda v:side*(v-b.close))-side*self.tick
        if side*(target-extreme)<=self.tick:return
        bid,ask=self.books[0] if len(self.books)==6 else (float('nan'),float('nan'))
        self.active=ForcedEpisode(f'{self.symbol}:FORCING:{key}:{touched}',side,touched,b.ts,
            source,key,tf,extreme,target,self.unit(),force,bid,ask)
        self.stats['observed_forcing_public_episode']+=1

    def advance(self,b,previous,evidence,common):
        episode=self.active
        if episode is None or b.ts<=episode.detected:return []
        side=episode.side
        reached=b.high>=episode.target if side>0 else b.low<=episode.target
        superseded=any(z.born>episode.detected and z.pivot_time>episode.detected and z.kind==-side
            and side*(z.price-episode.source)<0 for z in self.frames[15].pivots[-6:])
        if reached or superseded:
            self.active=None;self.stats['episode_objective_reached_or_replaced']+=1;return []
        episode.extreme=min(episode.extreme,b.low) if side>0 else max(episode.extreme,b.high)
        if episode.emitted or not evidence['ready']:return []
        adverse=evidence['sell_5'] if side>0 else evidence['buy_5']
        # Positive reported forcing is required. No sampled message does not
        # mean that all forced liquidation has ended.
        if adverse<=0:return []
        # The response controls the previous opposite candle's body. It can
        # precede full public-line recovery, unlike the former late-reclaim rule.
        body=side*(b.close-b.open)
        engulf=side*(previous.close-previous.open)<0 and side*(b.close-previous.open)>0
        if body<=0 or not engulf:return []
        bid=evidence['bid'];ask=evidence['ask']
        bid_change=math.log(bid/episode.initial_bid) if bid>0 and episode.initial_bid>0 else float('nan')
        ask_change=math.log(ask/episode.initial_ask) if ask>0 and episode.initial_ask>0 else float('nan')
        resupply=side*(bid_change-ask_change)
        if self.selection=='resupply' and not resupply>0:return []
        stop=episode.extreme-side*self.tick;target=episode.target
        risk=side*(b.close-stop);reward=side*(target-b.close)
        if risk<=self.tick or reward<risk:
            self.stats['response_geometry_unavailable']+=1;return []
        executed=sum(v.quote for v in list(self.history)[-5:])
        threshold=evidence['sell_threshold'] if side>0 else evidence['buy_threshold']
        f=dict(forcing_strength=math.log1p(adverse/max(threshold,1e-12)),
            forcing_share=adverse/max(executed,1e-12),
            reported_pressure=side*(evidence['buy_5']-evidence['sell_5'])/max(evidence['buy_5']+evidence['sell_5'],1e-12),
            response_body=body/max(b.high-b.low,self.tick),response_progress=body/episode.unit,
            recovery_fraction=side*(b.close-episode.extreme)/max(side*(target-episode.extreme),self.tick),
            defending_depth_change=bid_change if side>0 else ask_change,
            relative_depth_change=resupply,depth_skew=side*(bid-ask)/(bid+ask) if bid+ask>0 else float('nan'),
            common_response=side*common,context_direction=side*self.frames[60].direction(),
            source_scale=math.log2(episode.scale/5),source_distance=side*(b.close-episode.source)/episode.unit,
            elapsed_episode=math.log1p((b.ts-episode.started)/MINUTE),risk_range=risk/episode.unit,
            cost_r=.0006*(b.close+stop)/risk,planned_rr=reward/risk)
        episode.emitted=True;self.stats['plan']+=1
        return [Plan(f'{episode.key}:{b.ts}',episode.key,self.symbol,Side.LONG if side>0 else Side.SHORT,
            b.ts,episode.started,b.close,stop,target,reward/risk,episode.source,episode.scale,episode.source_id,
            'FROZEN_FIRST_LIQUIDITY_OR_TRANSACTION_VALUE',min(previous.open,previous.close),max(previous.open,previous.close),
            max(episode.extreme,target),min(episode.extreme,target),{k:float(v) for k,v in f.items()},
            family='OBSERVED_FORCED_SUPPLY_RESPONSE')]

    def observe(self,b,evidence,common):
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError('non-contiguous price clock')
        self.last_ts=b.ts;previous=self.history[-1] if self.history else b
        self.history.append(b);self.books.append((evidence['bid'],evidence['ask']))
        for frame in self.frames.values():
            for z in frame.pivots:
                if z.born<b.ts and z.key not in self.touches and (b.high>=z.price if z.kind>0 else b.low<=z.price):
                    self.touches[z.key]=b.ts
        plans=self.advance(b,previous,evidence,common)
        if self.active is None:self.seed(b,evidence)
        for frame in self.frames.values():frame.append(b)
        return plans

class ForcedResponsePolicy:
    def __init__(self,ticks,selection='response'):
        if selection not in ('response','resupply'):raise ValueError(selection)
        self.markets={s:ForcedMarket(s,t,selection) for s,t in ticks.items()};self.last_ts=0
    def observe(self,bars,forcing):
        if set(bars)!=set(self.markets) or set(forcing)!=set(bars) or len({v.ts for v in bars.values()})!=1:
            raise ValueError('incomplete synchronous forcing state')
        ts=next(iter(bars.values())).ts
        if ts<=self.last_ts:raise ValueError('non-increasing policy time')
        self.last_ts=ts
        moves={s:(b.close-list(self.markets[s].history)[-5].close)/self.markets[s].unit()
               for s,b in bars.items() if len(self.markets[s].history)>=5}
        plans=[]
        for s in sorted(bars):
            peers=[v for k,v in moves.items() if k!=s]
            plans+=self.markets[s].observe(bars[s],forcing[s],float(np.median(peers)) if peers else 0.)
        return plans
