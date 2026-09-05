"""A structural role is not an everlasting wall until its most distant wick.

The prior experiment had 800 higher-liquidity challenges but almost no entries:
footprints that price had already accepted through were still counted against
the newly established direction. Stops remain at the full causal wick. The role
of an old footprint is instead relinquished when ITS OWN timeframe closes
through its far body edge. This is a research translation of control transfer,
not a claim that the source defines wick invalidation and body acceptance as
identical concepts. No nearer genuinely unbroken obstacle is skipped.
"""
from liquidity_control import LiquidityMarket,LiquidityPolicy,FEATURES,MINUTE

class ReclaimedLiquidityMarket(LiquidityMarket):
    def _update(self,b):
        closed=super()._update(b)
        for z in self.zones:
            if not z.live or z.observed>=b.ts or z.scale not in closed:continue
            close=self.frames[z.scale][-1].close
            if (z.side>0 and close<z.low) or (z.side<0 and close>z.high):
                z.live=False;self.stats['opposing_role_relinquished']+=1
        return closed
    def _form_control(self,e,b):
        before=e.control_time
        super()._form_control(e,b)
        if not before and e.control_time:
            side=-e.source_kind
            forming=[x for x in self.five if e.started-5*MINUTE<=x.ts<=b.ts]
            if side>0:e.stop=min(e.stop,min(x.low for x in forming)-self.tick)
            else:e.stop=max(e.stop,max(x.high for x in forming)+self.tick)

class ReclaimedLiquidityPolicy(LiquidityPolicy):
    def __init__(self,ticks):
        self.markets={s:ReclaimedLiquidityMarket(s,t) for s,t in ticks.items()}
