"""Evolving auction policy bound to the existing Nautilus account lifecycle.

No stop movement, partial order, risk overlay, daily limit or trade-count cap.
A discretionary exit is a full market close when the conditional value of the
remaining auction is below the executable liquidation value. The source PDFs
permit a full close when an opposing structure removes the original premise;
the numeric continuation comparison is our research translation, not a quote.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from episode_states import EvolvingAuctions
from episode_decision import expected_log_gain
import research as r


class EpisodePolicy:
    def __init__(self,tape,seeds,belief,wait):
        self.auctions=EvolvingAuctions(tape,seeds);self.belief=belief;self.wait=wait
        self.probabilities={}
        self.markets={s:type('ObservedMarket',(),{'history':[],'stats':Counter(),'explanations':[]})() for s in tape.symbols}
    def observe(self,bars):
        for s,b in bars.items():self.markets[s].history.append(b)
        entries=self.auctions.observe(bars);now=self.auctions.last_ts
        states=[p for p in self.auctions.latest.values() if p.observed_time_ns==now]
        self.probabilities=dict(zip((p.plan_id for p in states),self.belief.probability(states),strict=True))
        if not self.wait:
            entries=[p for p in entries if self.auctions.seeds[self.auctions.root(p)].observed_time_ns==now]
        return entries


def strategy_type(tape,seeds,belief,scores,wait:bool,feedback:bool):
    base=r.AccountStrategy
    class EpisodeStrategy(base):
        def __init__(self,config,policy,liquidity,mark_at,router=None):
            episode_policy=EpisodePolicy(tape,seeds,belief,wait)
            super().__init__(config,episode_policy,liquidity,mark_at,router=None)
            self.router=self._entry_value
            self.alpha_closes={}
        def _instrument_for(self,symbol):
            return next(x for x in self.instruments.values() if x.raw_symbol.value==symbol)
        def _entry_value(self,p):
            inst=self._instrument_for(p.symbol);nav=self._current_nav()
            quantity=self._quantity(inst,p,nav)
            probability=float(self.policy.probabilities[p.plan_id])
            if quantity is None:
                scores[p.plan_id]=(-1.,probability);return -1.
            q=float(quantity);side=int(p.side.value);nav=float(nav)
            impact=self.liquidity.fraction(p.symbol,q,p.entry)
            entry=p.entry*(1+side*impact);stop=p.stop*(1-side*impact)
            win=nav+q*(side*(p.target-entry)-.0005*entry-.0002*p.target)
            loss=nav+q*(side*(stop-entry)-.0005*entry-.0005*stop)
            value=expected_log_gain(probability,win,loss,nav)
            scores[p.plan_id]=(value,probability)
            return value
        def on_bar(self,bar):
            super().on_bar(bar)
            now=int(bar.ts_event)
            if not feedback or self.bucket or self.active_plan is None or self.policy.auctions.last_ts!=now:
                return
            if self.emergency_exit_requested:return
            p=self.active_plan
            state=self.policy.auctions.state_for_position(p,now)
            if state is None:return
            positions=self.cache.positions_open()
            if len(positions)!=1:return
            position=positions[0]
            if int(position.ts_opened)>=now:return
            probability=float(self.policy.probabilities[state.plan_id])
            side=int(p.side.value);q=float(position.quantity);entry=float(position.avg_px_open)
            cash=float(self._current_nav());price=state.entry
            impact=self.liquidity.fraction(p.symbol,q,price)
            liquidation=price*(1-side*impact);stop=p.stop*(1-side*impact)
            now_nav=cash+q*(side*(liquidation-entry)-.0005*liquidation)
            target_nav=cash+q*(side*(p.target-entry)-.0002*p.target)
            stop_nav=cash+q*(side*(stop-entry)-.0005*stop)
            value=expected_log_gain(probability,target_nav,stop_nav,now_nav)
            if value>=0:return
            self.alpha_closes[p.plan_id]={'exit_decision':'AUCTION_CONTINUATION_VALUE_BELOW_FULL_LIQUIDATION',
                                         'exit_probability':probability,'exit_continuation_value':value}
            # Reuse RE1's protective cleanup contract. The existing flag also
            # suppresses protective resubmission during an intended full close.
            self.emergency_exit_requested=True
            self.expected_cancel_ids.update(self._protective_ids())
            iid=self.active_instrument_id
            self.cancel_all_orders(iid)
            if not self.portfolio.is_flat(iid):self.close_all_positions(iid)
        def on_position_closed(self,event):
            plan_id=self.active_plan.plan_id if self.active_plan is not None else None
            super().on_position_closed(event)
            if plan_id is not None and self.closed:
                self.closed[-1].update(self.alpha_closes.get(plan_id,{'exit_decision':'ORIGINAL_STRUCTURAL_BOUNDARY'}))
    return EpisodeStrategy
