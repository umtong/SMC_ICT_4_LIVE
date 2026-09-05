"""Only the entry decision changes; RE1 still owns fills and protection.

A pending order is canceled after a completed minute closes beyond the response
extreme without a fill. This ends the first-return opportunity, not the trading
day; there is no trade quota, daily loss limit, holding-time stop or partial.
"""
from decimal import Decimal,ROUND_DOWN
from nautilus_trader.model.enums import OrderSide,TimeInForce
from domain import Side
import run_control_v2 as base

class PassiveAccountStrategy(base.AccountStrategy):
    def _quantity(self,instrument,plan,nav):
        q=float(nav)*.03/abs(plan.entry-plan.stop)
        for _ in range(8):
            slip=self.liquidity.fraction(plan.symbol,q,plan.entry)
            per=abs(plan.entry-plan.stop)+plan.entry*.0002+plan.stop*(.0005+slip)+plan.entry*.0001
            q=float(nav)*.03/per
        step=Decimal(str(instrument.size_increment))
        floored=(Decimal(str(q))/step).to_integral_value(rounding=ROUND_DOWN)*step
        if floored<Decimal(str(instrument.min_quantity)):return None
        if instrument.max_quantity is not None and floored>Decimal(str(instrument.max_quantity)):return None
        return instrument.make_qty(floored)
    def _submit_plan(self,iid,p):
        if p.causal_event_id in self.used_events or p.interaction_time_ns<=self.last_close:return False
        inst=self.instruments[iid];nav=self._current_nav();q=self._quantity(inst,p,nav)
        if q is None:return False
        last=self.policy.markets[p.symbol].history[-1]
        if int(p.side.value)*(last.close-p.entry)<=float(inst.price_increment):return False
        order=self.order_factory.limit(instrument_id=iid,
            order_side=OrderSide.BUY if p.side is Side.LONG else OrderSide.SELL,
            quantity=q,price=inst.make_price(p.entry),time_in_force=TimeInForce.GTC,
            post_only=True,reduce_only=False,tags=[f'PLAN:{p.plan_id}','ROLE:ENTRY'])
        self.active_plan=p;self.active_instrument_id=iid;self.active_entry_id=order.client_order_id
        self.active_stop_id=None;self.active_target_id=None;self.entry_cancel_requested=False
        self.emergency_exit_requested=False;self.protection_submitted=False;self.position_closed_seen=False
        self.expected_cancel_ids.clear();self.cleanup_pending_ids.clear()
        self.used_events.add(p.causal_event_id);self.submit_order(order)
        self._record('passive_entry_submitted',plan_id=p.plan_id,entry=p.entry,stop=p.stop,target=p.target,
                     quantity=str(q),risk_budget=float(nav)*.03)
        return True
    def on_bar(self,bar):
        p=self.active_plan
        if p is not None and bar.bar_type.instrument_id==self.active_instrument_id and self._portfolio_flat():
            order=self.cache.order(self.active_entry_id)
            peak=p.features['pending_cancel_price']
            if order is not None and not order.is_closed and int(p.side.value)*(float(bar.close)-peak)>0:
                self.decisions.append({'plan_id':p.plan_id,'ts':int(bar.ts_event),'score':0.,'reason':'first_return_ended_unfilled'})
                self.entry_cancel_requested=True;self.cancel_order(order)
        super().on_bar(bar)
