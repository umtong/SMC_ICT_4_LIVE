"""Single-slot after-cost NAV accounting for candidate-03."""
from __future__ import annotations
from model import Bar,Direction,EntryPlan,ExitReason,Position,ScenarioState,StrategyConfig,Trade
from strategy_common import Emit,NS_PER_MINUTE

class PortfolioSimulator:
    """One-slot after-cost NAV accounting using the project's loss-budget formula."""
    def __init__(self,config:StrategyConfig,emit:Emit)->None:
        self.config=config;self.emit=emit;self.nav=config.initial_nav;self.position:Position|None=None;self.trades:list[Trade]=[]
    def open(self,plan:EntryPlan,bar:Bar,index:int)->Position|None:
        if self.position is not None:raise RuntimeError("single-slot constraint violated")
        fee=self.config.taker_fee_bps/10_000;slip=self.config.slippage_bps/10_000
        entry_fill=plan.entry_price*(1+plan.direction.sign*slip);stop_fill=plan.stop_price*(1-plan.direction.sign*slip)
        expected_funding_per_unit=entry_fill*(self.config.funding_bps_per_8h/10_000)*(self.config.max_holding_bars/480)
        per_unit_loss=abs(entry_fill-stop_fill)+entry_fill*fee+stop_fill*fee+expected_funding_per_unit
        if per_unit_loss<=0:return None
        planned=self.nav*self.config.risk_fraction;quantity=planned/per_unit_loss;entry_fee=quantity*entry_fill*fee
        position=Position(plan.scenario_id,plan.kind,plan.direction,bar.close_time_ns,index,entry_fill,plan.stop_price,
                          plan.target_price,quantity,self.nav,planned,entry_fee,entry_fill,entry_fill,plan.pool_id)
        self.position=position
        self.emit(scenario_id=plan.scenario_id,event_type="POSITION_ACCOUNTING_OPEN",event_time_ns=bar.close_time_ns,
                  observed_time_ns=bar.close_time_ns,previous_state=ScenarioState.POSITION_ACTIVE.value,
                  next_state=ScenarioState.POSITION_ACTIVE.value,reason_code="RISK_BUDGET_QUANTITY",
                  reference_price=entry_fill,details={"quantity":quantity,"nav_before":self.nav,
                  "risk_fraction":self.config.risk_fraction,"planned_loss":planned,"expected_stop_fill":stop_fill,
                  "expected_funding_per_unit":expected_funding_per_unit,"per_unit_planned_loss":per_unit_loss})
        return position
    def on_bar(self,bar:Bar,index:int)->Trade|None:
        p=self.position
        if p is None:return None
        if p.direction is Direction.LONG:
            p.max_favorable_price=max(p.max_favorable_price,bar.high);p.max_adverse_price=min(p.max_adverse_price,bar.low)
            stop_hit=bar.low<=p.stop_price;target_hit=bar.high>=p.target_price
        else:
            p.max_favorable_price=min(p.max_favorable_price,bar.low);p.max_adverse_price=max(p.max_adverse_price,bar.high)
            stop_hit=bar.high>=p.stop_price;target_hit=bar.low<=p.target_price
        # Intrabar ordering is unknown in one-minute OHLC; adverse stop wins ties.
        if stop_hit:return self._close(p.stop_price,bar,ExitReason.STOP)
        if target_hit:return self._close(p.target_price,bar,ExitReason.TARGET)
        if index-p.entry_index>=self.config.max_holding_bars:return self._close(bar.close,bar,ExitReason.OPPORTUNITY_EXPIRED)
        return None
    def force_close(self,bar:Bar)->Trade|None:
        return self._close(bar.close,bar,ExitReason.END_OF_RUN) if self.position is not None else None
    def _close(self,intended:float,bar:Bar,reason:ExitReason)->Trade:
        p=self.position;assert p is not None
        fee=self.config.taker_fee_bps/10_000;slip=self.config.slippage_bps/10_000
        exit_fill=intended*(1-p.direction.sign*slip);exit_fee=p.quantity*exit_fill*fee
        price_pnl=p.quantity*p.direction.sign*(exit_fill-p.entry_price)
        holding=(bar.close_time_ns-p.entry_time_ns)/NS_PER_MINUTE
        funding=p.quantity*p.entry_price*(self.config.funding_bps_per_8h/10_000)*max(0.0,holding)/480
        net=price_pnl-p.entry_cost-exit_fee-funding;nav_after=p.nav_before+net
        if nav_after<=0:raise RuntimeError("NAV became non-positive")
        raw_risk=abs(p.entry_price-p.stop_price)
        if p.direction is Direction.LONG:mfe=p.max_favorable_price-p.entry_price;mae=p.entry_price-p.max_adverse_price
        else:mfe=p.entry_price-p.max_favorable_price;mae=p.max_adverse_price-p.entry_price
        trade=Trade(p.scenario_id,p.kind,p.direction,p.pool_id,p.entry_time_ns,bar.close_time_ns,p.entry_price,exit_fill,
                    p.stop_price,p.target_price,p.quantity,p.nav_before,nav_after,net,net/p.planned_loss,holding,reason,
                    mfe/raw_risk if raw_risk>0 else 0.0,mae/raw_risk if raw_risk>0 else 0.0)
        self.nav=nav_after;self.position=None;self.trades.append(trade)
        self.emit(scenario_id=trade.scenario_id,event_type="NAV_REALIZED",event_time_ns=bar.close_time_ns,
                  observed_time_ns=bar.close_time_ns,previous_state=ScenarioState.POSITION_ACTIVE.value,
                  next_state=ScenarioState.POSITION_ACTIVE.value,reason_code=reason.value,reference_price=exit_fill,
                  details={"net_pnl":net,"net_r":trade.net_r,"nav_before":p.nav_before,"nav_after":nav_after,
                  "holding_minutes":holding,"funding_cost":funding})
        return trade
