"""Reuse an existing project solution to irrelevant line/channel construction.

These two owners only read confirmed pivots, bars and horizontal snapshots.
Their stop, target, confirmation and event identity are not changed here.
"""
from __future__ import annotations
import auction_reuse_policy as a
from easychart_re1_efficient_objective import PivotOnlyObjectiveBook

OriginalAuctionMarket=a.AuctionMarket

class FastAuctionMarket(OriginalAuctionMarket):
    def __init__(self,symbol,tick,external=None):
        super().__init__(symbol,tick,external)
        reject=self.owners['REJECTION']
        for name,tf in (('five',5),('fifteen',15),('sixty',60)):
            setattr(reject,name,PivotOnlyObjectiveBook(symbol,tf,tick,pivot_spans=(2,6)))
        accept=self.owners['ACCEPTANCE']
        accept.local_structure=PivotOnlyObjectiveBook(symbol,15,tick,pivot_spans=(2,6))
        accept.decision_structure=PivotOnlyObjectiveBook(symbol,5,tick,pivot_spans=(2,6))


def check_equivalence(tape):
    symbol=tape.symbols[0];tick=tape.ticks[symbol]
    ordinary=OriginalAuctionMarket(symbol,tick,tape.feature_mark_at)
    fast=FastAuctionMarket(symbol,tick,tape.feature_mark_at)
    left=[];right=[]
    def key(p):return (p.plan_id,p.causal_event_id,p.observed_time_ns,p.entry,p.stop,p.target,p.gross_rr,p.features)
    for row in tape.raw[symbol].iloc[:4320].itertuples(index=False):
        b=a.Observation(int(row.ts),float(row.open),float(row.high),float(row.low),float(row.close),
                        float(row.volume),float(row.taker_buy_volume),float(row.quote_volume),int(row.count),float(row.taker_buy_quote_volume))
        # No cross-asset state is used to generate geometry in either owner.
        market={5:0.,15:0.,60:0.}
        left.extend(ordinary.observe(b,market));right.extend(fast.observe(b,market))
    assert len(left)==len(right),(len(left),len(right))
    import numpy as np
    for x,y in zip(left,right,strict=True):
        assert key(x)[:-1]==key(y)[:-1],(key(x)[:-1],key(y)[:-1])
        assert x.features.keys()==y.features.keys()
        assert np.allclose(list(x.features.values()),list(y.features.values()),equal_nan=True)
    print('PIVOT_BOOK_EQUIVALENCE',symbol,len(left),'unchanged causal plans',flush=True)


def install():
    a.AuctionMarket=FastAuctionMarket
