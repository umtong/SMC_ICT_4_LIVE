"""Source-price experiment: a preplaced first-return limit, not an extra pattern.

Direction still starts with fresh higher liquidity and its completed local
response. Once the footprint exists, place the single order at its nearest edge.
Stop includes all forming wicks; target is the first opposing structure. No entry
is assumed from the bar that created the order, and no entry is chased later.
"""
import numpy as np
from astra_policy import Plan
from domain import Side
from local_response import LocalResponseMarket
from liquidity_control import LiquidityPolicy,FEATURES

class PassiveResponseMarket(LocalResponseMarket):
    def observe(self,b):
        super().observe(b)
        e=self.episode
        if e is None or e.finished or e.control_time!=b.ts:return None
        e.finished=True
        side=-e.source_kind
        entry=e.zone_high if side>0 else e.zone_low
        risk=side*(entry-e.stop)
        if risk<=self.tick:return None
        levels=[(e.departure,'CONTROL_DEPARTURE'),(e.reference_value,'PRIOR_AUCTION_TRADED_VALUE'),
                (e.reference_other,'PRIOR_AUCTION_OPPOSITE')]
        for tf,book in self.books.items():
            for p in book.pivots[-40:]:
                if p.pivot_id not in self.touched and p.side==('HIGH' if side>0 else 'LOW'):
                    levels.append((p.price,f'UNSPENT_{tf}M_SWING'))
        for z in self.zones:
            if z.live and z.side!=side:
                if z.low<=entry<=z.high:
                    self.stats['limit_inside_opposing_zone']+=1;return None
                levels.append((z.low if side>0 else z.high,f'OPPOSING_{z.scale}M_FOOTPRINT'))
        levels=[(p,k) for p,k in levels if side*(p-entry)>self.tick]
        if not levels:return None
        target,kind=min(levels,key=lambda item:side*(item[0]-entry));target-=side*self.tick
        rr=side*(target-entry)/risk
        if rr<1:
            self.stats['limit_first_obstacle_less_than_one_r']+=1
            self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'preplaced_limit_geometry',
                                      'entry':entry,'stop':e.stop,'target':target,'rr':rr})
            return None
        f={k:np.nan for k in FEATURES};f.update(e.impulse_context)
        unit=f.pop('unit');f.update(planned_rr=rr,risk_range=risk/unit,cost_r=.0008*entry/risk,
            obstacle_distance=side*(target-entry)/unit,risk_bps=10000*risk/entry,
            auction_value_distance=side*(e.reference_value-entry)/unit,
            entry_displacement=side*(b.close-entry)/unit,pending_cancel_price=e.departure)
        self.stats['passive_plans']+=1
        region=self.history[e.start_index:]
        return Plan(f'{self.symbol}:PASSIVE:{e.key}:{b.ts}',f'{self.symbol}:LQC:{e.key}',self.symbol,
                    Side.LONG if side>0 else Side.SHORT,b.ts,e.started,entry,e.stop,target,rr,e.level,e.scale,
                    e.key,kind,e.zone_low,e.zone_high,max(x.high for x in region),min(x.low for x in region),
                    f,family='FRESH_LIQUIDITY_PASSIVE_RESPONSE')

class PassiveResponsePolicy(LiquidityPolicy):
    def __init__(self,ticks):self.markets={s:PassiveResponseMarket(s,t) for s,t in ticks.items()}
