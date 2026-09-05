"""Native Nautilus orders, fills, positions, OCO and account accounting.
Backtest plans contain only decisions computed from completed observations;
no outcome columns are accepted. Paper streaming must call the same submit
method with newly completed observations rather than precomputed schedules.
"""
from decimal import Decimal,ROUND_DOWN
import numpy as np
import pandas as pd
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import OrderSide,OrderType,TimeInForce,TriggerType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.currencies import USDT

class AuctionExecution(Strategy):
    def __init__(self,plans,instruments,bar_types,costs,marks,end):
        super().__init__(StrategyConfig(strategy_id='ASTRA2-001'))
        allowed=['symbol','side','ts','entry','stop','target','episode','scale','source','root_ts','entry_kind','predicted_value','rr']
        self.plans=plans[[k for k in allowed if k in plans]].copy()
        self.instruments=instruments; self.bar_types=bar_types; self.costs=costs; self.marks=marks; self.end=end
        self.active=None; self.entry_id=None; self.used=set(); self.entered=False; self.closing=False
        self.trades=[]; self.fills=[]; self.rejections=[]; self.nav_path=[]; self.last_nav_time=-1
    def balance(self): return float(self.portfolio.account(Venue('BINANCE')).balance_total(USDT))
    def on_start(self):
        self.costs.clock=self.clock
        for bt in self.bar_types.values(): self.subscribe_bars(bt)
        self.groups={int(t):q.to_dict('records') for t,q in self.plans.groupby('ts')}
        for t in self.groups:
            self.clock.set_time_alert(name=f'plan-{t}',alert_time=pd.Timestamp(t+1,tz='UTC'),callback=self.on_decision)
    def on_decision(self,event):
        t=int(event.name.split('-')[1]); choices=self.groups[t]
        if self.active is not None or self.cache.positions_open(): return
        choices=sorted(choices,key=lambda x:(-x.get('predicted_value',0),-x['scale'],x['symbol']))
        for row in choices:
            if row['episode'] in self.used: continue
            if self.submit(row): break
    def submit(self,row):
        inst=self.instruments[row['symbol']]; s=row['side']; now=self.clock.timestamp_ns()
        entry=float(inst.make_price(row['entry'])); stop=float(inst.make_price(row['stop'])); target=float(inst.make_price(row['target']))
        distance=s*(entry-stop)
        if distance<=0 or s*(target-entry)<distance: return False
        nav=self.balance(); limit=row.get('entry_kind')=='limit'; q=nav*.03/distance
        for _ in range(15):
            adverse=self.costs.surcharge(row['symbol'],q*stop,now)
            entry_cost=.0002 if limit else .0005+self.costs.surcharge(row['symbol'],q*entry,now)
            unit=distance+entry*entry_cost+stop*(.0005+adverse)+float(inst.price_increment)
            q=nav*.03/unit
        step=inst.size_increment.as_decimal(); quantity=(Decimal(str(q))/step).to_integral_value(rounding=ROUND_DOWN)*step
        if quantity<=0 or (inst.min_quantity and quantity<inst.min_quantity.as_decimal()): return False
        if inst.max_quantity and quantity>inst.max_quantity.as_decimal(): return False
        kw=dict(instrument_id=inst.id,order_side=OrderSide.BUY if s==1 else OrderSide.SELL,quantity=inst.make_qty(quantity),time_in_force=TimeInForce.GTC,entry_order_type=OrderType.LIMIT if limit else OrderType.MARKET,entry_post_only=limit,sl_trigger_price=inst.make_price(stop),tp_price=inst.make_price(target),tp_post_only=False,emulation_trigger=TriggerType.NO_TRIGGER,entry_tags=['ROLE:ENTRY'],sl_tags=['ROLE:STOP'],tp_tags=['ROLE:TARGET'])
        if limit: kw['entry_price']=inst.make_price(entry)
        orders=self.order_factory.bracket(**kw)
        self.active=dict(row,entry=entry,stop=stop,target=target,nav_before=nav,quantity=float(quantity),planned_risk_pct=float(quantity)*unit/nav*100)
        self.entry_id=orders.first.client_order_id; self.entered=False; self.closing=False
        self.submit_order_list(orders)
        return True
    def on_bar(self,bar):
        t=int(bar.ts_event)
        if self.active and str(bar.bar_type.instrument_id)==str(self.instruments[self.active['symbol']].id) and not self.entered:
            p=self.active; hit=(float(bar.high)>=p['target'] or float(bar.low)<=p['stop']) if p['side']==1 else (float(bar.low)<=p['target'] or float(bar.high)>=p['stop'])
            if hit and not self.closing:
                self.closing=True; self.cancel_all_orders(self.instruments[p['symbol']].id)
        minute=t//60000000000
        if minute!=self.last_nav_time:
            self.last_nav_time=minute; nav=self.balance()
            for p in self.cache.positions_open():
                symbol=str(p.instrument_id).split('-PERP')[0]
                mark=float(self.marks[symbol].close.asof(pd.Timestamp(t,tz='UTC')))
                nav+=float(p.unrealized_pnl(self.instruments[symbol].make_price(mark)))
            self.nav_path.append({'ts':t,'nav':nav})
    def on_order_filled(self,event):
        role='entry' if event.client_order_id==self.entry_id else 'exit'
        self.fills.append({'ts':int(event.ts_event),'symbol':str(event.instrument_id),'role':role,'price':float(event.last_px),'quantity':float(event.last_qty),'cost':float(event.commission)})
        if role=='entry' and self.active:
            self.entered=True; self.active['entry_ts']=int(event.ts_event); self.used.add(self.active['episode'])
    def on_position_closed(self,event):
        if self.active:
            row=dict(self.active); row.update(exit_ts=int(event.ts_event),nav_after=self.balance())
            row['net_r']=(row['nav_after']/row['nav_before']-1)/.03
            row['hold_minutes']=(row['exit_ts']-row.get('entry_ts',row['ts']))/60000000000
            self.trades.append(row)
        self.active=None; self.entry_id=None; self.entered=False; self.closing=False
    def on_order_canceled(self,event):
        if event.client_order_id==self.entry_id and not self.entered:
            self.active=None; self.entry_id=None; self.closing=False
    def on_order_expired(self,event): self.on_order_canceled(event)
    def failure(self,event):
        self.rejections.append({'ts':int(event.ts_event),'reason':str(event.reason)})
        if self.active:
            inst=self.instruments[self.active['symbol']]
            self.cancel_all_orders(inst.id)
            if self.entered: self.close_all_positions(inst.id)
            else: self.active=None; self.entry_id=None
    def on_order_rejected(self,event): self.failure(event)
    def on_order_denied(self,event): self.failure(event)
    def on_stop(self):
        for inst in self.instruments.values(): self.cancel_all_orders(inst.id)
