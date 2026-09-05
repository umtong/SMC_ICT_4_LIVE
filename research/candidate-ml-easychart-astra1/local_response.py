"""Local response at higher liquidity, rather than proof after the move is spent.

The preceding candidate required a distant pre-challenge 5m swing to break,
then a return to the original footprint. That imposed two distinct confirmations
not required by the source, often after the nearby objective was already used.
Here the source's completed engulfing footprint provides the local response:
we still require fresh higher liquidity, a close back through that boundary,
then a later actual return. The old opposing pivot remains a measured obstacle,
not a mandatory late-entry gate. No new RR cap, risk modifier or time exit.
"""
import math
import numpy as np
from reclaimed_liquidity import ReclaimedLiquidityMarket
from liquidity_control import LiquidityPolicy,FEATURES,MINUTE

class LocalResponseMarket(ReclaimedLiquidityMarket):
    def _form_control(self,e,b):
        side=-e.source_kind
        if len(self.five)<25 or side*(b.close-e.level)<=self.tick:return
        previous=self.five[-2]
        body=side*(b.close-b.open);opposing=-side*(previous.close-previous.open)
        engulf=(opposing>2*self.tick and body>=2*opposing and side*(b.close-previous.open)>0)
        x,m,z=self.five[-3:]
        gap=(abs(m.close-m.open)>=2*max(abs(x.close-x.open),abs(z.close-z.open),self.tick)
             and ((side>0 and z.low>x.high) or (side<0 and z.high<x.low)))
        if not (engulf or gap):return
        sequence=[x for x in self.five if x.ts>=e.started-5*MINUTE]
        if not sequence:return
        origin=min(range(len(sequence)),key=lambda j:sequence[j].low if side>0 else -sequence[j].high)
        impulse=sequence[origin:]
        if engulf:lo,hi=min(previous.open,previous.close),max(previous.open,previous.close)
        else:lo,hi=(x.high,z.low) if side>0 else (z.high,x.low)
        if hi-lo<self.tick or side*(b.close-(hi if side>0 else lo))<=0:return
        unit=max(float(np.mean([x.high-x.low for x in self.five[-25:-1]])),2*self.tick)
        event=self.history[e.start_index:];volume=sum(x.volume for x in event)
        baseline=max(float(np.mean([x.volume for x in self.five[-25:-1]])),1e-12)
        vol=sum(x.volume for x in impulse);ranges=sum(x.high-x.low for x in impulse)
        e.stop=(min(e.extreme,min(x.low for x in sequence))-self.tick if side>0
                else max(e.extreme,max(x.high for x in sequence))+self.tick)
        e.zone_low=lo;e.zone_high=hi;e.control_time=b.ts
        e.control_index=len(self.history)-1;e.departure=b.high if side>0 else b.low
        e.impulse_context={'unit':unit,'context_15':side*self.direction(15),'context_60':side*self.direction(60),
            'sweep_15':float(e.scale==15),'sweep_60':float(e.scale==60),
            'impulse_range':side*(b.close-impulse[0].open)/unit,
            'impulse_efficiency':side*(b.close-impulse[0].open)/max(ranges,self.tick),
            'impulse_activity':vol/max(len(impulse)*baseline,1e-12),
            'impulse_flow':side*sum(x.delta for x in impulse)/max(vol,1e-12),
            'opponent_reclaimed':side*(b.close-e.level)/unit,
            'footprint_gap':float(gap),'footprint_body':abs(previous.close-previous.open)/unit,
            'break_strength':side*(b.close-e.control_price)/unit,
            'source_age':e.parent_age,'parent_scale':math.log(e.scale/5),
            'parent_range':abs(e.reference_other-e.level)/unit,'parent_age':e.parent_age,
            'transfer_distance':side*(e.control_price-e.extreme)/unit,
            'event_activity':volume/max(len(event)*self.current_volume,1e-12),
            'event_flow':side*sum(x.delta for x in event)/max(volume,1e-12),
            'event_progress':side*(b.close-e.level)/unit}
        self.stats['local_footprint_response']+=1

class LocalResponsePolicy(LiquidityPolicy):
    def __init__(self,ticks):self.markets={s:LocalResponseMarket(s,t) for s,t in ticks.items()}
