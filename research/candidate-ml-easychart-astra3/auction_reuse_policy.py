"""Two outcomes of a public liquidity auction, one account-level decision policy.

Reuse existing causal engineering for: (1) a completed H4 extreme rejected,
(2) a 15m accepted direction and the first held 5m pullback. Neither the C-branch
router, its fixed-1R target, category exclusions nor its cost cap is reused.
Each source-derived structural stop/target is retained unchanged.
"""
from dataclasses import dataclass,replace
from collections import Counter
import math
import numpy as np
from astra_policy import Observation as BaseObservation,Plan,MINUTE
from policy import Market as GeometryMarket,Frame,Level,Challenge,FEATURES as BASE_FEATURES
from easychart_re1_h4_liquidity import H4LiquiditySweepEngine
from easychart_re1_efficient_pullback import EfficientPullbackEngine
from easychart_re1_flow import FlowCandle

FEATURES=BASE_FEATURES+('auction_rejection','higher_strength','lower_strength','trigger_strength',
                       'setup_age','trigger_age','overlap_width','peer_progress','peer_flow')

@dataclass(slots=True)
class Observation(BaseObservation):
    buy_quote: float=0.

class AuctionMarket(GeometryMarket):
    def __init__(self,symbol,tick,external=None):
        super().__init__(symbol,tick,external)
        self.owners={'REJECTION':H4LiquiditySweepEngine(symbol,tick,1.),
                     'ACCEPTANCE':EfficientPullbackEngine(symbol,tick,1.)}
        self.minute_rows=[]
    @staticmethod
    def candle(rows):
        b=rows[-1]
        return FlowCandle(ts_close_ns=b.ts,open=rows[0].open,high=max(v.high for v in rows),
                          low=min(v.low for v in rows),close=b.close,volume=sum(v.volume for v in rows),
                          quote_volume=sum(v.quote for v in rows),trade_count=sum(v.trades for v in rows),
                          taker_buy_base_volume=sum(v.buy for v in rows),taker_buy_quote_volume=sum(v.buy_quote for v in rows))
    def observe(self,b,market):
        if self.last_ts and b.ts-self.last_ts!=MINUTE:raise ValueError('non-contiguous completed-auction clock')
        self.last_ts=b.ts;self.history.append(b);self.minute_rows.append(b)
        self.bases.append(1e4*(b.close/self.external(self.symbol,b.ts)-1) if self.external else float('nan'))
        for frame in self.frames.values():frame.append(b)
        emitted=[]
        for tf in (60,15,5,1):
            if b.ts//MINUTE%tf or len(self.minute_rows)<tf:continue
            candle=self.candle(self.minute_rows[-tf:])
            for owner,engine in self.owners.items():
                for p in engine.on_bar(tf,candle):emitted.append((owner,p))
                engine.drain_trace()
        self.minute_rows=self.minute_rows[-60:]
        result=[]
        for owner,p in emitted:
            if len(self.five)<48:continue
            side=int(p.side.value)
            region=[v for v in self.history if p.interaction_time_ns<=v.ts<=p.observed_time_ns]
            if not region:raise ValueError('missing actual interaction observations')
            engine=self.owners[owner]
            z=engine.find_zone(p.higher_zone_id)
            level=float((z.lower+z.upper)/2) if z else (p.overlap_lower+p.overlap_upper)/2
            kind=-side if owner=='REJECTION' else side
            source=Level(p.higher_zone_id,level,kind,p.higher_timeframe_minutes,
                         p.setup_observed_time_ns,p.setup_observed_time_ns,p.higher_strength_ratio)
            c=Challenge(p.causal_event_id,source,p.interaction_time_ns,max(v.high for v in region),min(v.low for v in region),
                        sum(v.volume for v in region),sum(v.buy for v in region),
                        max(np.mean([v.volume for v in self.history[-61:-1]]),1e-12),self.unit(),p.target)
            f=self._features(c,b,side,p.stop,p.target,market)
            f['source_strength']=p.higher_strength_ratio
            f.update(auction_rejection=float(owner=='REJECTION'),higher_strength=p.higher_strength_ratio,
                lower_strength=p.lower_strength_ratio,trigger_strength=p.trigger_strength_ratio,
                setup_age=math.log1p(max(0.,(b.ts-p.setup_observed_time_ns)/MINUTE)),
                trigger_age=math.log1p(max(0.,(b.ts-p.trigger_time_ns)/MINUTE)),
                overlap_width=(p.overlap_upper-p.overlap_lower)/c.unit)
            result.append(Plan(f'{owner}:{p.plan_id}',f'{self.symbol}:{p.causal_event_id}',self.symbol,p.side,
                p.observed_time_ns,p.interaction_time_ns,p.entry,p.stop,p.target,p.gross_rr,level,p.higher_timeframe_minutes,
                p.higher_zone_id,str(p.target_zone_kind),p.overlap_lower,p.overlap_upper,c.high,c.low,f,
                family=f'AUCTION_{owner}'))
            self.stats[f'{owner.lower()}_plan']+=1
        return result

class LiquidityPolicy:
    def __init__(self,ticks,external=None,micro=None):
        self.markets={s:AuctionMarket(s,t,external) for s,t in ticks.items()};self.last_ts=0
    def observe(self,bars):
        if set(bars)!=set(self.markets):raise ValueError('incomplete universe')
        stamps={b.ts for b in bars.values()}
        if len(stamps)!=1:raise ValueError('unequal universe clocks')
        ts=stamps.pop()
        if ts<=self.last_ts:raise ValueError('non-increasing universe clock')
        self.last_ts=ts;market={}
        for n in (5,15,60):
            values=[(b.close-self.markets[s].history[-n].close)/self.markets[s].unit()
                    for s,b in bars.items() if len(self.markets[s].history)>=n]
            market[n]=float(np.median(values)) if values else 0.
        plans=[p for s in sorted(bars) for p in self.markets[s].observe(bars[s],market)]
        out=[]
        for p in plans:
            side=int(p.side.value);peers=[m for s,m in self.markets.items() if s!=p.symbol and len(m.history)>=16]
            f=dict(p.features)
            f.update(peer_progress=float(np.median([side*(m.history[-1].close-m.history[-16].close)/m.unit() for m in peers])),
                     peer_flow=float(np.median([side*sum(b.delta for b in m.history[-15:])/max(sum(b.volume for b in m.history[-15:]),1e-12) for m in peers])))
            out.append(replace(p,features=f))
        return out
