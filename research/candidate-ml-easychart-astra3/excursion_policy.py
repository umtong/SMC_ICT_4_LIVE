"""An actual sweep is an excursion, not necessarily a large close-to-close return.

The former washout detector selected large net moves and therefore omitted the
very long-wick rejection described in EasyChart Fakeout pp1,6,10. This module
changes that event measurement, keeping the same observed source, recovery,
pre-entry invalidation/objective, and one-entry episode rules.

Three prior-volatility units remains an unfitted research definition of an
exceptional excursion. It is not a claim made by EasyChart. The optional test
entry represents a second adverse auction failing to renew the original low /
high; it is a distinct entry decision, not an after-the-fact PnL filter.
"""
from __future__ import annotations
import math
from washout_policy import Washout, WashoutMarket, WashoutPolicy, FEATURES as BASE_FEATURES

FEATURES=BASE_FEATURES+('net_to_excursion','extreme_age','test_effort','test_depth')

class ExcursionMarket(WashoutMarket):
    require_test=False

    def _seed(self,b,x,rows,sigma):
        if len(rows)<241 or sigma<=0:return
        first=rows[-6];impulse=rows[-5:]
        choices=[]
        sources=self._sources(rows,first.ts,b)
        for direction in (-1,1):
            eligible=[z for z in sources if z[0]==direction]
            if not eligible:continue
            extreme_bar=(max(impulse,key=lambda v:v.high) if direction>0 else min(impulse,key=lambda v:v.low))
            extreme=extreme_bar.high if direction>0 else extreme_bar.low
            excursion=direction*math.log(extreme/first.close)
            if excursion/sigma<3.:continue
            source=max(eligible,key=lambda z:(z[3],z[4]))
            choices.append((excursion/sigma,direction,extreme,extreme_bar.ts,source))
        if not choices:return
        zscore,direction,extreme,extreme_ts,source=max(choices,key=lambda item:item[0])
        _,level,key,tf,touched=source;side=-direction
        prior=rows[-21:-6];baseline_volume=sum(v.volume for v in prior)
        if baseline_volume<=0:return
        anchor=sum(v.quote for v in prior)/baseline_volume
        if side*(anchor-b.close)<=self.tick:return
        # A completed recovery AFTER the known extreme ends that particular
        # excursion. Earlier highs in the impulse cannot end a later washout.
        if any((v.high>=anchor if side>0 else v.low<=anchor) for v in impulse if v.ts>extreme_ts):return
        volume=sum(v.volume for v in impulse)
        premium=x.get('x_premium',float('nan'))
        change=x.get('x_premium_change',float('nan'))
        e=Washout(f'{self.symbol}:EXCURSION:{key}:{touched}',side,b.ts,touched,level,key,tf,
            extreme,anchor,max(self.tick,first.close*sigma),zscore,
            side*sum(v.delta for v in impulse)/max(volume,1e-12),
            (volume/5)/max(baseline_volume/15,1e-12),premium-change,premium,
            x.get('x_oi_change_15',float('nan')),side*x.get('x_spot_move_15',float('nan')))
        e.net_to_excursion=direction*math.log(b.close/first.close)/max(zscore*sigma,1e-12)
        e.extreme_age=(b.ts-extreme_ts)/60_000_000_000
        e.first_response=False;e.reaction_peak=b.high if side>0 else b.low
        e.test_started=0;e.test_volume=0.;e.test_count=0;e.test_extreme=b.close
        e.initial_extreme=extreme;e.test_effort=0.;e.test_depth=0.
        self.active=e;self.stats['exceptional_public_excursion']+=1

    def _advance(self,b,previous,x,peer):
        e=self.active
        if e is None:return []
        if not self.require_test:
            plans=super()._advance(b,previous,x,peer)
        else:
            side=e.side
            recovered=(b.high>=e.anchor if side>0 else b.low<=e.anchor)
            if recovered:
                self.active=None;self.stats['value_recovered_before_test']+=1;return []
            # Before the first reaction the actual washout can still extend.
            if not e.first_response:
                e.extreme=min(e.extreme,b.low) if side>0 else max(e.extreme,b.high)
                reclaimed=side*(b.close-e.source)>0
                response=b.close>previous.high if side>0 else b.close<previous.low
                if reclaimed and response:
                    e.first_response=True;e.initial_extreme=e.extreme
                    e.reaction_peak=b.high if side>0 else b.low
                    self.stats['first_recovery_waiting_for_test']+=1
                return []
            if b.low<=e.initial_extreme if side>0 else b.high>=e.initial_extreme:
                self.active=None;self.stats['renewed_adverse_auction_broke_original_extreme']+=1;return []
            if not e.test_started:
                starts=(b.close<previous.low if side>0 else b.close>previous.high)
                if not starts:
                    e.reaction_peak=max(e.reaction_peak,b.high) if side>0 else min(e.reaction_peak,b.low)
                    return []
                e.test_started=b.ts;e.test_extreme=b.low if side>0 else b.high
            e.test_volume+=b.volume;e.test_count+=1
            e.test_extreme=min(e.test_extreme,b.low) if side>0 else max(e.test_extreme,b.high)
            if b.ts<=e.test_started:return []
            # Two actually observed auction legs now exist. Keep the original
            # washout invalidation; do not manufacture RR with a tighter stop.
            e.test_depth=side*(e.reaction_peak-e.test_extreme)/max(side*(e.reaction_peak-e.initial_extreme),self.tick)
            baseline=e.activity
            prior=list(self.history)[-66:-6]
            volume_rate=sum(v.volume for v in prior)/max(len(prior),1)
            e.test_effort=(e.test_volume/e.test_count)/max(volume_rate*baseline,1e-12)
            plans=super()._advance(b,previous,x,peer)
        from dataclasses import replace
        result=[]
        for p in plans:
            f=dict(p.features)
            f.update(net_to_excursion=e.net_to_excursion,extreme_age=e.extreme_age,
                     test_effort=e.test_effort,test_depth=e.test_depth)
            result.append(replace(p,features=f,family='TESTED_PUBLIC_EXCURSION' if self.require_test else 'PUBLIC_EXCURSION_RECLAIM'))
        return result

class TestedExcursionMarket(ExcursionMarket):
    require_test=True

class ExcursionPolicy(WashoutPolicy):
    def __init__(self,ticks,require_test=False):
        market=TestedExcursionMarket if require_test else ExcursionMarket
        self.markets={s:market(s,t) for s,t in ticks.items()};self.last_ts=0
