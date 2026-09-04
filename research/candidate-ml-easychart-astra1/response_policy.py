"""Same liquidity-control hypothesis, but buy/sell a response PRICE, not its late close.

The direction still comes from a previously observable public liquidity event.
OB/FVG objects locate the one entry; they are not independent strategy families.
Opposing displacement created during the episode can shorten its destination.
"""
from __future__ import annotations
from decimal import Decimal,ROUND_DOWN
import numpy as np
from domain import Candle,Side
from easychart_zones import EasyChartZoneDetector,ZoneSide
from astra_policy import AstraPolicy,Market,Observation,Plan,MINUTE
from execution import AstraStrategy
from nautilus_trader.model.enums import OrderSide,TimeInForce

class ResponseMarket(Market):
    def __init__(self,symbol,tick):
        super().__init__(symbol,tick)
        self.zone_books={tf:EasyChartZoneDetector(symbol,tf,tick) for tf in (1,5)}
        self.new_zones=[];self.counter_zones=[]
    def _detect(self,tf,b):
        book=self.zone_books[tf]
        prior=book.bars[-48:]
        normal=float(np.median([abs(x.close-x.open) for x in prior])) if prior else 0.
        book.bars.append(Candle(ts_close_ns=b.ts,open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume))
        i=len(book.bars)-1
        zones=[z for z in (book._detect_order_block(i),book._detect_fvg(i)) if z is not None]
        if tf==5:
            # A meaningful opposing displacement, not every tiny swing high.
            for z in zones:
                formation=[book.bars[j] for j in z.formation_indices]
                body=max(abs(x.close-x.open) for x in formation)
                if z.high_quality_by_size and body>=normal:self.counter_zones.append(z)
        return zones
    def observe(self,b,market_progress=0.):
        self.new_zones=self._detect(1,b)
        self.counter_zones=[z for z in self.counter_zones if not ((z.side==ZoneSide.SUPPORT and b.close<z.invalidation) or (z.side==ZoneSide.RESISTANCE and b.close>z.invalidation))]
        if b.ts//MINUTE%5==0 and len(self.aggregate[5])==4:
            self._detect(5,self.merge(self.aggregate[5]+[b]))
        return super().observe(b,market_progress)
    def _advance(self,b,market_progress):
        output=[];i=len(self.history)-1
        for side,c in list(self.pending.items()):
            if b.ts<=c.started:continue
            if (side>0 and b.low<=c.stop) or (side<0 and b.high>=c.stop):
                self._explain(c,'control_invalidated',b.ts);del self.pending[side];continue
            if c.prior_obstacle is not None and ((side>0 and b.high>=c.prior_obstacle) or (side<0 and b.low<=c.prior_obstacle)):
                self._explain(c,'destination_spent_before_entry',b.ts);del self.pending[side];continue
            if not c.detached:
                c.departure_volume+=b.volume;c.departure_minutes+=1
                c.departure_high=max(c.departure_high,b.high);c.departure_low=min(c.departure_low,b.low)
                if side*(b.close-c.level)>(c.zone_high-c.zone_low)/2:c.detached=True
                continue
            touch=b.low<=c.zone_high if side>0 else b.high>=c.zone_low
            if not c.returned:
                if not touch:
                    c.departure_volume+=b.volume;c.departure_minutes+=1
                    c.departure_high=max(c.departure_high,b.high);c.departure_low=min(c.departure_low,b.low)
                    continue
                c.returned=True;c.return_index=i;c.return_time=b.ts;c.return_open=b.open
                self.stats['first_return']+=1
            c.return_volume+=b.volume;c.return_buy+=b.buy
            peak=c.departure_high if side>0 else c.departure_low
            if (side>0 and b.high>=peak) or (side<0 and b.low<=peak):
                self._explain(c,'response_already_completed_first_objective',b.ts);del self.pending[side];continue
            support=ZoneSide.SUPPORT if side>0 else ZoneSide.RESISTANCE
            zones=[z for z in self.new_zones if z.side==support]
            if not zones:continue
            # The public defended boundary projected into the actual body/gap
            # is the price, rather than an arbitrary 50% retracement or R target.
            zone=min(zones,key=lambda z:abs((z.lower+z.upper)/2-c.level))
            entry=float(np.clip(c.level,zone.lower,zone.upper))
            if side*(b.close-entry)<=self.tick:continue
            stop=min(c.stop,zone.invalidation) if side>0 else max(c.stop,zone.invalidation)
            targets=[(x,'OPPOSING_LIQUIDITY') for x in self._targets(side,entry,b.ts)]
            targets.append((peak,'DEPARTURE_EXTREME'))
            blocked=False
            for z in self.counter_zones:
                if z.observed_time_ns<c.started or z.observed_time_ns>b.ts or z.side==support:continue
                if z.lower<=entry<=z.upper:blocked=True;break
                frontier=z.lower if side>0 else z.upper
                if side*(frontier-entry)>self.tick:targets.append((frontier,'OPPOSING_DISPLACEMENT'))
            if blocked:
                self._explain(c,'opposing_control_at_entry',b.ts)
                continue
            target,kind=min(targets,key=lambda x:side*(x[0]-entry));target-=side*self.tick
            risk=side*(entry-stop);rr=side*(target-entry)/risk if risk>0 else -1.
            if rr<1.:
                # Do not turn the first weak one-minute bounce into a permanent
                # rejection. The same untouched setup can still offer its price.
                self._explain(c,'response_zone_geometry_below_one_r',b.ts)
                continue
            features=self._feature_record(c,b,entry,stop,target,market_progress)
            features['cost_r']=(.0002*entry+.0005*stop+.0001*stop)/risk
            features.update(zone_strength=float(zone.strength_ratio),zone_width_range=(zone.upper-zone.lower)/c.unit)
            p=Plan(f'{c.key}:ZONE:{b.ts}',c.key,self.symbol,Side.LONG if side>0 else Side.SHORT,b.ts,c.started,entry,stop,target,rr,c.level,c.scale,c.source_id,kind,zone.lower,zone.upper,c.departure_high,c.departure_low,features,'BOUNDARY_CONTROL_RESPONSE_ZONE')
            output.append(p);self.stats['plan']+=1;self._explain(c,'plan_emitted',b.ts);del self.pending[side]
        return output

class ResponsePolicy(AstraPolicy):
    def __init__(self,ticks):
        self.markets={s:ResponseMarket(s,t) for s,t in ticks.items()};self.last_ts=0

class ResponseStrategy(AstraStrategy):
    def _quantity(self,instrument,plan,nav):
        s=instrument.raw_symbol.value;e=plan.entry;t=plan.stop
        q=float(nav)*.03/max(abs(e-t),float(instrument.price_increment))
        for _ in range(8):
            f=self.liquidity.fraction(s,q,t)
            per=abs(e-t)+.0002*e+(.0005+f)*t+.0001*e
            q=float(nav)*.03/per
        step=Decimal(str(instrument.size_increment))
        q=(Decimal(str(q))/step).to_integral_value(rounding=ROUND_DOWN)*step
        if q<Decimal(str(instrument.min_quantity)):return None
        if instrument.max_quantity is not None and q>Decimal(str(instrument.max_quantity)):return None
        return instrument.make_qty(q)
    def _submit_plan(self,instrument_id,plan):
        inst=self.instruments[instrument_id];nav=self._current_nav();q=self._quantity(inst,plan,nav)
        if q is None:return False
        order=self.order_factory.limit(instrument_id=instrument_id,order_side=OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL,quantity=q,price=inst.make_price(plan.entry),time_in_force=TimeInForce.GTC,post_only=True,reduce_only=False,tags=[f'PLAN:{plan.plan_id}','ROLE:ENTRY'])
        self.active_plan=plan;self.active_instrument_id=instrument_id;self.active_entry_id=order.client_order_id
        self.active_stop_id=None;self.active_target_id=None;self.entry_cancel_requested=False;self.emergency_exit_requested=False
        self.protection_submitted=False;self.position_closed_seen=False;self.expected_cancel_ids.clear();self.cleanup_pending_ids.clear()
        self.submit_order(order)
        self._record('submitted',plan_id=plan.plan_id,entry_client_order_id=str(order.client_order_id),entry_price=plan.entry,quantity=str(q),nav_at_submission=float(nav))
        return True
    def on_bar(self,bar):
        p=self.active_plan
        if p is not None and bar.bar_type.instrument_id==self.active_instrument_id and self.portfolio.is_flat(self.active_instrument_id):
            side=1 if p.side is Side.LONG else -1
            invalid=float(bar.low)<=p.stop if side>0 else float(bar.high)>=p.stop
            completed=float(bar.high)>=p.target if side>0 else float(bar.low)<=p.target
            if invalid or completed:
                order=self.cache.order(self.active_entry_id)
                if order is not None and not order.is_closed:
                    self._record('unfilled_response_expired',plan_id=p.plan_id,reason='invalidation' if invalid else 'destination_reached')
                    self.cancel_order(order)
        super().on_bar(bar)
