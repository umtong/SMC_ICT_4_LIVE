"""One decision at the first completed response, including a no-trade decision.

The original origin policy kept looking for a later response after rejecting the
first response's geometry. That is not a first-return policy: worsening price can
mechanically improve nominal RR while the original response has already failed.
This correction expires the event after its first complete price/volume response
whether or not a valid order can be emitted. No threshold or RR target is tuned.
"""
from auction_control_survival import AuctionMarket, AuctionControlPolicy, FEATURES

class FirstResponseMarket(AuctionMarket):
    def observe(self,b):
        previous=self.history[-1] if self.history else None
        plan=super().observe(b)
        if plan is not None:return plan
        z=self.control
        if previous is None or z is None or z.consumed or not z.live:return None
        if z.observed>=b.ts or not z.returned or z.adverse_volume<=0:return None
        reclaim=z.side*(b.close-z.adverse_value/z.adverse_volume)>self.tick
        response=b.close>previous.high if z.side>0 else b.close<previous.low
        if reclaim and response:
            z.consumed=True
            self.stats['first_complete_response_no_trade']+=1
        return None

class FirstAuctionResponse(AuctionControlPolicy):
    def __init__(self,ticks):self.markets={s:FirstResponseMarket(s,t) for s,t in ticks.items()}
