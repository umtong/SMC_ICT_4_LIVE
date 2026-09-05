"""Observations of structure, not another entry/exit rule collection.

OB pp4-5 and Fakeout/Trap p11 describe a smaller liquidity event in a larger
pre-existing footprint. Channel p12 distinguishes a small countertrend flag
from its parent trend. This module attaches that missing context to EXISTING
auction proposals without moving their entries, stops or targets.

Distances are continuous, normalized by the proposal's own structural risk.
There is no required number of confluences and no category exclusion list.
All snapshots are formed from completed minutes, before outcomes are read.
"""
from __future__ import annotations
from dataclasses import replace
import math
from collections import deque
import numpy as np
from policy import Market,Frame
from auction_reuse_policy import Observation

CONTEXT_FEATURES=tuple(f'struct_{tf}_{k}' for tf in (15,60,240) for k in
    ('defense','obstacle','failed_defense','failed_obstacle','liquidity_balance','channel_location','channel_slope','direction'))
FEATURES=('risk_bps','cost_r','planned_rr','source_scale','source_strength','event_age','penetration','event_flow','event_activity',
          'auction_rejection','trigger_strength','peer_progress','peer_flow','x_spot_flow_15','x_spot_flow_60',
          'x_relative_move_15','x_oi_change_15','x_premium')+CONTEXT_FEATURES


class StructureContext(Market):
    def __init__(self,symbol,tick):
        super().__init__(symbol,tick)
        self.frames[240]=Frame(240)
        self.failed=deque(maxlen=128)
    def observe_context(self,b):
        self.history.append(b)
        for f in self.frames.values():
            for z in f.levels:
                if z.born<b.ts and (b.high>=z.price if z.kind>0 else b.low<=z.price):z.consumed=True
        previous=[z for z in self.zones if z['alive']]
        self._update_zones(b)
        for z in previous:
            if z.get('invalidated',False):self.failed.append((b.ts,dict(z)))
        for tf in sorted(self.frames,reverse=True):
            if self.frames[tf].append(b) is not None:self._new_zones(tf)
    def snapshot(self,p):
        side=int(p.side.value);risk=abs(p.entry-p.stop);now=p.observed_time_ns
        if risk<=0:raise ValueError('non-positive proposal risk')
        output={}
        def signed_distance(z):
            if z['low']<=p.entry<=z['high']:return 0.
            edge=z['low'] if p.entry<z['low'] else z['high']
            return side*(p.entry-edge)/risk
        def nearest(zones):
            return min(zones,key=lambda z:abs(signed_distance(z))) if zones else None
        for tf in (15,60,240):
            prefix=f'struct_{tf}_';frame=self.frames[tf]
            valid=[z for z in self.zones if z['alive'] and z['tf']==tf and z['born']<p.interaction_time_ns]
            aligned=nearest([z for z in valid if z['side']==side])
            opposed=nearest([z for z in valid if z['side']==-side])
            failures=[z for t,z in self.failed if z['tf']==tf and now-t<=tf*4*60_000_000_000]
            ownfail=nearest([z for z in failures if z['side']==side])
            oppfail=nearest([z for z in failures if z['side']==-side])
            output[prefix+'defense']=signed_distance(aligned) if aligned else float('nan')
            output[prefix+'obstacle']=signed_distance(opposed) if opposed else float('nan')
            output[prefix+'failed_defense']=signed_distance(ownfail) if ownfail else float('nan')
            output[prefix+'failed_obstacle']=signed_distance(oppfail) if oppfail else float('nan')
            high=[z.price for z in frame.levels if not z.consumed and z.kind==1 and z.price>p.entry]
            low=[z.price for z in frame.levels if not z.consumed and z.kind==-1 and z.price<p.entry]
            output[prefix+'liquidity_balance']=side*((min(high)-p.entry)-(p.entry-max(low)))/risk if high and low else float('nan')
            channel=frame.channel(now)
            output[prefix+'channel_location']=side*(2*(p.entry-channel[1])/(channel[2]-channel[1])-1) if channel else float('nan')
            output[prefix+'channel_slope']=side*channel[3]*tf*60_000_000_000/risk if channel else float('nan')
            output[prefix+'direction']=side*frame.direction()
        return output


def attach_context(tape,plans):
    by_time={}
    for p in plans:by_time.setdefault(p.observed_time_ns,[]).append(p)
    books={s:StructureContext(s,tape.ticks[s]) for s in tape.symbols}
    arrays={s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count','taker_buy_quote_volume']].to_numpy() for s,d in tape.raw.items()}
    output=[]
    for i in range(len(next(iter(arrays.values())))):
        for s,a in arrays.items():
            t,o,h,l,c,v,b,q,n,bq=a[i]
            books[s].observe_context(Observation(int(t),o,h,l,c,v,b,q,int(n),bq))
        for p in by_time.get(int(t),[]):output.append(replace(p,features={**p.features,**books[p.symbol].snapshot(p)}))
    if len(output)!=len(plans):raise ValueError('proposal observation time is missing from context')
    return output
