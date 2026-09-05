"""Nautilus 1.230 simulation adapters using its FeeModel/SimulationModule APIs.
Assumptions: maker 2bp, taker 5bp; market execution surcharge 2bp plus
10bp*sqrt(notional / preceding one-minute quote volume). This surcharge is
booked as an execution cost, not claimed as an observed exchange commission.
"""
import math
import numpy as np
from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.backtest.modules import SimulationModule
from nautilus_trader.backtest.config import SimulationModuleConfig
from nautilus_trader.model.enums import LiquiditySide,PositionSide
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT

class ExecutionCosts(FeeModel):
    def __init__(self,frames):
        self.clock=None; self.records=[]
        self.volumes={s:(d.index.asi8,d.quote_volume.to_numpy(float)) for s,d in frames.items()}
    def surcharge(self,symbol,notional,time):
        ts,volume=self.volumes[symbol]
        i=np.searchsorted(ts,time,side='left')-1
        if i<0: raise ValueError('Execution requires preceding observed liquidity')
        return .0002+.001*math.sqrt(notional/max(volume[i],1.))
    def get_commission(self,order,fill_qty,fill_px,instrument):
        maker=order.liquidity_side==LiquiditySide.MAKER
        notional=float(fill_qty)*float(fill_px)
        symbol=str(instrument.id).split('-PERP')[0]
        time=self.clock.timestamp_ns()
        commission=notional*(.0002 if maker else .0005)
        execution=0. if maker else notional*self.surcharge(symbol,notional,time)
        self.records.append({'ts':int(time),'symbol':symbol,'commission':commission,'execution_cost':execution,'notional':notional,'maker':maker})
        return Money(commission+execution,USDT)

class HistoricalFunding(SimulationModule):
    def __init__(self,fundings,marks):
        super().__init__(SimulationModuleConfig())
        self.events=sorted((int(t.value),s,float(rate),float(marks[s].close.asof(t))) for s,series in fundings.items() for t,rate in series.items())
        self.cursor=0; self.records=[]
    def pre_process(self,data):
        now=int(data.ts_event)
        while self.cursor<len(self.events) and self.events[self.cursor][0]<=now:
            time,symbol,rate,mark=self.events[self.cursor]; self.cursor+=1
            for position in self.exchange.cache.positions_open():
                if str(position.instrument_id)!=f'{symbol}-PERP.BINANCE': continue
                side=1 if position.side==PositionSide.LONG else -1
                amount=-side*float(position.quantity)*mark*rate
                self.exchange.adjust_account(Money(amount,USDT))
                self.records.append({'ts':time,'symbol':symbol,'cashflow':amount,'rate':rate,'mark':mark})
    def process(self,ts_now): pass
    def log_diagnostics(self,logger): pass
    def reset(self): self.cursor=0; self.records=[]
