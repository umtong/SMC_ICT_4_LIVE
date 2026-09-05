"""Full premise exits; original stop, target and 3% quantity remain unchanged.

Source: OB pp4-5 permits a full close at a newly opposing OB/structure. The
supplied transaction notes and GGFqHk_JPDI 21:59-22:08 describe full liquidation
when favorable liquidity collection is accompanied by increased volume.

Translation: a newly completed opposing five-minute OB/FVG, or penetration of
an already-known local structural price during a top-decile activity minute.
The activity quantile and five-minute decision scale are research assumptions.
No minimum profit, stop movement, holding cap, daily rule or partial exit.
This module is used only by the research BacktestEngine, not a live broker.
"""
from __future__ import annotations
from collections import deque
import numpy as np
import research as r
from auction_reuse_policy import Observation
from structure_context import StructureContext


def strategy_type(tape):
    base=r.AccountStrategy
    class SourceExitStrategy(base):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.exit_books={s:StructureContext(s,tape.ticks[s]) for s in tape.symbols}
            self.activity={s:deque(maxlen=60) for s in tape.symbols}
            self.exit_reasons={};self.trade_obstacles={};self.current_bars={}
        def _submit_plan(self,iid,p):
            book=self.exit_books[p.symbol]
            levels=[z.price for tf in (5,15) for z in book.frames[tf].levels
                    if z.born<p.observed_time_ns and p.side.value*(z.price-p.entry)>tape.ticks[p.symbol]]
            answer=super()._submit_plan(iid,p)
            if answer:self.trade_obstacles[p.plan_id]=levels
            return answer
        def on_bar(self,bar):
            inst=self.instruments[bar.bar_type.instrument_id];symbol=inst.raw_symbol.value
            b=Observation(int(bar.ts_event),float(bar.open),float(bar.high),float(bar.low),float(bar.close),
                          float(bar.volume),float(bar.taker_buy_base_volume),float(bar.quote_volume),
                          int(bar.count),float(bar.taker_buy_quote_volume))
            history=self.activity[symbol]
            high_activity=len(history)==60 and b.quote>float(np.quantile(history,.9))
            history.append(b.quote);self.current_bars[symbol]=(b,high_activity)
            self.exit_books[symbol].observe_context(b)
            super().on_bar(bar)
            if self.bucket or self.active_plan is None or self.emergency_exit_requested:return
            p=self.active_plan;now=int(bar.ts_event)
            if now<=p.observed_time_ns:return
            b,high_activity=self.current_bars[p.symbol];side=int(p.side.value)
            opposite=any(z['tf']==5 and z['born']==now and z['side']==-side
                         for z in self.exit_books[p.symbol].zones)
            collected=any((b.high>=level if side>0 else b.low<=level)
                          for level in self.trade_obstacles.get(p.plan_id,()))
            reason='NEW_OPPOSING_5M_STRUCTURE' if opposite else 'LOCAL_LIQUIDITY_COLLECTION_WITH_ACTIVITY' if collected and high_activity else None
            if reason is not None:
                self.exit_reasons[p.plan_id]=reason
                self._request_emergency_flatten(reason)
        def on_position_closed(self,event):
            key=self.active_plan.plan_id if self.active_plan is not None else None
            super().on_position_closed(event)
            if key is not None and self.closed:self.closed[-1]['exit_decision']=self.exit_reasons.get(key,'PREENTRY_BOUNDARY')
    return SourceExitStrategy
