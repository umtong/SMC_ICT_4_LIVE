"""Nautilus binding. Reuses RE1's full-entry/protective-order lifecycle.

Funding uses the same SimulationModule/adjust_account mechanism as Nautilus'
FXRolloverInterestModule (v1.230.0). Slippage is an explicit research assumption,
not a measured order-book estimate: 1 bp plus 2 bp * sqrt(minute participation).
"""
from __future__ import annotations
from collections import defaultdict,deque
from decimal import Decimal,ROUND_DOWN
import math
import numpy as np
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.backtest.modules import SimulationModule
from nautilus_trader.backtest.config import SimulationModuleConfig
from nautilus_trader.config import BacktestEngineConfig,LoggingConfig,RiskEngineConfig
from nautilus_trader.model.enums import AccountType,OmsType,BookType,OrderSide,OrderType
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.identifiers import Venue,TraderId
from nautilus_trader.model.objects import Currency,Money
from execution_re1 import EasyChartRE1Strategy,EasyChartMTFConfig
from astra_policy import AstraPolicy,Observation,MINUTE

VENUE=Venue('BINANCE');USDT=Currency.from_str('USDT')

class ExecutionLiquidity(FillModel):
    def __init__(self,stress=1.):
        super().__init__(prob_fill_on_limit=1.,prob_slippage=0.,random_seed=31)
        self.activity=defaultdict(lambda:deque(maxlen=15));self.stress=stress
        self.bar_open={}
    def observe(self,symbol,b):self.activity[symbol].append(max(b.quote,b.volume*b.close))
    def fraction(self,symbol,qty,price):
        history=self.activity[symbol]
        if not history:raise RuntimeError('liquidity must be observed before execution')
        turnover=max(float(np.mean(history)),1.)
        return self.stress*(.0001+.0002*math.sqrt(max(0.,qty*price/turnover)))
    def get_orderbook_for_fill_simulation(self,instrument,order,best_bid,best_ask):
        if best_bid is None or best_ask is None:return None
        book=OrderBook(instrument_id=instrument.id,book_type=BookType.L2_MBP)
        q=float(order.leaves_qty);symbol=instrument.raw_symbol.value
        if order.order_type==OrderType.LIMIT:
            tick=float(instrument.price_increment);limit=float(order.price)
            crosses=(float(best_bid)>limit+tick*.5) if order.side==OrderSide.SELL else (float(best_ask)<limit-tick*.5)
            if not crosses:return book
            bid=ask=instrument.make_price(limit)
        else:
            base_bid,base_ask=float(best_bid),float(best_ask)
            if order.order_type==OrderType.STOP_MARKET:
                # A 1m OHLC path jumps from open to an extreme. A stop crossed
                # along that segment is not a market order submitted AT the
                # extreme. Use its first crossing, retaining real open gaps.
                if str(instrument.id) not in self.bar_open:raise RuntimeError('stop has no execution-bar open')
                bar_open=self.bar_open[str(instrument.id)]
                trigger=float(order.trigger_price)
                base=(max(trigger,bar_open) if order.side==OrderSide.BUY else min(trigger,bar_open))
                base_bid=base_ask=base
            f=self.fraction(symbol,q,(base_bid+base_ask)/2)
            bid=instrument.make_price(base_bid*(1-f))
            ask=instrument.make_price(base_ask*(1+f))
        size=instrument.make_qty(q)
        book.add(BookOrder(side=OrderSide.BUY,price=bid,size=size,order_id=1),ts_event=0)
        book.add(BookOrder(side=OrderSide.SELL,price=ask,size=size,order_id=2),ts_event=0)
        return book

class FundingCashflows(SimulationModule):
    def __init__(self,records,mark_at):
        super().__init__(SimulationModuleConfig())
        self.records=sorted(records);self.mark_at=mark_at;self.cursor=0;self.payments=[]
        self.execution_liquidity=None
    def process(self,ts_now):
        while self.cursor<len(self.records) and self.records[self.cursor][0]<=ts_now:
            ts,symbol,rate=self.records[self.cursor];self.cursor+=1
            for pos in self.exchange.cache.positions_open():
                inst=self.exchange.instruments[pos.instrument_id]
                if inst.raw_symbol.value!=symbol or pos.ts_opened>=ts:continue
                price=self.mark_at(symbol,ts)
                cash=-float(pos.quantity)*price*rate*(1 if pos.is_long else -1)
                self.exchange.adjust_account(Money(cash,USDT))
                self.payments.append({'ts':ts,'symbol':symbol,'position_id':str(pos.id),'cash':cash,'rate':rate,'mark':price})
    def pre_process(self,data):
        # Execution-only open; never exposed to the forecasting policy. No
        # high/low/close/volume from the future execution bar is used here.
        if self.execution_liquidity is not None and hasattr(data,'bar_type'):
            self.execution_liquidity.bar_open[str(data.bar_type.instrument_id)]=float(data.open)
    def log_diagnostics(self,logger):pass
    def reset(self):self.cursor=0;self.payments=[]

def make_engine(funding,liquidity,starting_nav=100000.):
    funding.execution_liquidity=liquidity
    e=BacktestEngine(BacktestEngineConfig(trader_id=TraderId('ASTRA-001'),logging=LoggingConfig(log_level='ERROR'),risk_engine=RiskEngineConfig(bypass=False)))
    e.add_venue(venue=VENUE,oms_type=OmsType.NETTING,account_type=AccountType.MARGIN,
                starting_balances=[Money(starting_nav,USDT)],base_currency=USDT,default_leverage=Decimal('100'),
                fill_model=liquidity,modules=[funding],bar_execution=True,bar_adaptive_high_low_ordering=True)
    return e

class AstraStrategy(EasyChartRE1Strategy):
    def __init__(self,config,policy:AstraPolicy,liquidity:ExecutionLiquidity,mark_at,router=None):
        super().__init__(config)
        self.policy=policy;self.liquidity=liquidity;self.mark_at=mark_at;self.router=router
        self.bucket={};self.all_plans=[];self.decisions=[];self.nav_path=[];self.open_context={};self.closed=[]
        self.used_intervals=[];self.stopping_at_boundary=False
    def on_start(self):
        for iid,bt in zip(self.config.instrument_ids,self.config.execution_bar_types,strict=True):
            self.instruments[iid]=self.cache.instrument(iid)
            if self.instruments[iid] is None:raise RuntimeError(f'missing instrument {iid}')
            self.subscribe_bars(bt)
    def _execution_reserves(self,instrument):
        p=self.policy.markets[instrument.raw_symbol.value].history[-1].close
        return Decimal(str(p*.0002)),Decimal(str(p*.0002))
    def _quantity(self,instrument,plan,nav):
        s=instrument.raw_symbol.value;entry=plan.entry;stop=plan.stop
        q=float(nav)*.03/max(abs(entry-stop),float(instrument.price_increment))
        for _ in range(8):
            f=self.liquidity.fraction(s,q,entry)
            per=abs(entry-stop)+(entry+stop)*(.0005+f)+entry*.0001
            q=float(nav)*.03/per
        step=Decimal(str(instrument.size_increment))
        floored=(Decimal(str(q))/step).to_integral_value(rounding=ROUND_DOWN)*step
        if floored<Decimal(str(instrument.min_quantity)):return None
        if instrument.max_quantity is not None and floored>Decimal(str(instrument.max_quantity)):return None
        return instrument.make_qty(floored)
    def _nav(self,ts):
        nav=float(self._current_nav())
        for p in self.cache.positions_open():
            inst=self.instruments[p.instrument_id]
            price=inst.make_price(self.mark_at(inst.raw_symbol.value,ts))
            nav+=p.unrealized_pnl(price).as_double()
        return nav
    def on_bar(self,bar):
        iid=bar.bar_type.instrument_id
        inst=self.instruments[iid];s=inst.raw_symbol.value
        b=Observation(int(bar.ts_event),float(bar.open),float(bar.high),float(bar.low),float(bar.close),float(bar.volume),
                      float(bar.taker_buy_base_volume),float(bar.quote_volume),int(bar.count))
        if self.bucket and next(iter(self.bucket.values())).ts!=b.ts:raise RuntimeError('incomplete synchronized account clock')
        self.bucket[s]=b;self.liquidity.observe(s,b)
        if len(self.bucket)!=len(self.instruments):return
        plans=self.policy.observe(self.bucket);self.bucket={}
        if b.ts<self.config.trading_start_ns:return
        self.all_plans.extend(plans)
        ranked=[]
        for p in plans:
            score=(self.router(p) if self.router else 1.-p.features['cost_r'])
            reason='candidate' if score>0 else 'decision_declined'
            if any(a<=p.observed_time_ns and p.interaction_time_ns<=z and side==p.side.value for a,z,side in self.used_intervals):
                reason='same_overlapping_causal_episode'
            self.decisions.append({'plan_id':p.plan_id,'ts':p.observed_time_ns,'score':float(score),'reason':reason})
            if reason=='candidate':ranked.append((score,p))
        ranked.sort(key=lambda x:(-x[0],x[1].interaction_time_ns,x[1].symbol))
        if self.active_plan is None and self._portfolio_flat():
            for score,p in ranked:
                iid=next(i for i in self.instruments if self.instruments[i].raw_symbol.value==p.symbol)
                self.plan_log[p.plan_id]=p
                self.open_context[p.plan_id]={'nav_before':float(self._current_nav()),'score':float(score)}
                if self._submit_plan(iid,p):
                    self.used_intervals.append((p.interaction_time_ns,p.observed_time_ns,p.side.value))
                    self.decisions.append({'plan_id':p.plan_id,'ts':p.observed_time_ns,'score':float(score),'reason':'selected'})
                    break
        else:
            for score,p in ranked:self.decisions.append({'plan_id':p.plan_id,'ts':p.observed_time_ns,'score':float(score),'reason':'global_position_occupied'})
        self.nav_path.append((b.ts,self._nav(b.ts)))
    def on_position_closed(self,event):
        p=self.active_plan
        if p is not None:
            ctx=self.open_context[p.plan_id]
            row=p.record();row.update(ctx)
            row.update({'position_id':str(event.position_id),'opened':int(event.ts_opened),'closed':int(event.ts_closed),
                        'entry_fill':float(event.avg_px_open),'exit_fill':float(event.avg_px_close),
                        'quantity':float(event.peak_qty),'pnl_ex_funding':event.realized_pnl.as_double(),
                        'holding_minutes':(event.ts_closed-event.ts_opened)/MINUTE,
                        'evaluation_censored':self.stopping_at_boundary})
            self.closed.append(row)
            for j,(a,z,side) in enumerate(self.used_intervals):
                if a==p.interaction_time_ns and side==p.side.value:self.used_intervals[j]=(a,max(z,int(event.ts_closed)),side)
        super().on_position_closed(event)
    def on_stop(self):
        self.stopping_at_boundary=True
        for iid,bt in zip(self.config.instrument_ids,self.config.execution_bar_types,strict=True):
            self.cancel_all_orders(iid)
            if not self.portfolio.is_flat(iid):self.close_all_positions(iid)
            self.unsubscribe_bars(bt)
