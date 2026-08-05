"""Candidate-03 replay coordinator and diagnostics."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from math import log
from statistics import median
from typing import Any
from model import Bar,ScenarioKind,StrategyConfig
from liquidity_detector import LiquidityDetector
from auction_scenario import AuctionScenarioEngine
from portfolio_simulator import PortfolioSimulator
from strategy_common import Emit,NS_PER_DAY,utc_date_key

class Candidate03:
    """Complete single-instrument state machine used by staged BTC screening."""
    def __init__(self,config:StrategyConfig,emit:Emit)->None:
        config.validate();self.config=config;self.detector=LiquidityDetector(config,emit)
        self.scenarios=AuctionScenarioEngine(config,self.detector,emit);self.portfolio=PortfolioSimulator(config,emit)
    def run(self,bars:list[Bar],trade_start_ns:int,trade_end_ns:int)->dict[str,Any]:
        if not bars:raise ValueError("bars cannot be empty")
        last_trade_bar:Bar|None=None
        for index,bar in enumerate(bars):
            in_window=trade_start_ns<=bar.close_time_ns<trade_end_ns
            if in_window:last_trade_bar=bar
            closed=self.portfolio.on_bar(bar,index)
            if closed is not None:self.scenarios.mark_closed(bar,closed.exit_reason);self.scenarios.reset_if_terminal()
            sweeps=self.detector.on_bar(bar,index)
            if not in_window:continue
            if self.portfolio.position is None:
                self.scenarios.reset_if_terminal();self.scenarios.consider_sweeps(sweeps,index)
                plan=self.scenarios.on_bar(bar,index)
                if plan is not None:self.portfolio.open(plan,bar,index)
        if last_trade_bar is None:raise ValueError("trade window has no bars")
        forced=self.portfolio.force_close(last_trade_bar)
        if forced is not None:self.scenarios.mark_closed(last_trade_bar,forced.exit_reason)
        return self.metrics(trade_start_ns,trade_end_ns)
    def metrics(self,start_ns:int,end_ns:int)->dict[str,Any]:
        trades=self.portfolio.trades;days=max(1.0,(end_ns-start_ns)/NS_PER_DAY);final=self.portfolio.nav
        wins=[t for t in trades if t.net_pnl>0];losses=[t for t in trades if t.net_pnl<=0]
        peak=self.config.initial_nav;max_dd=0.0;daily_nav:dict[str,float]={}
        for t in trades:
            peak=max(peak,t.nav_after);max_dd=max(max_dd,1-t.nav_after/peak);daily_nav[utc_date_key(t.exit_time_ns)]=t.nav_after
        day_cursor=datetime.fromtimestamp(start_ns/1e9,tz=timezone.utc).date();end_day=datetime.fromtimestamp(end_ns/1e9,tz=timezone.utc).date()
        nav=self.config.initial_nav;daily_returns=[]
        while day_cursor<end_day:
            key=day_cursor.isoformat();next_nav=daily_nav.get(key,nav);daily_returns.append({"date":key,"nav":next_nav,"return":next_nav/nav-1})
            nav=next_nav;day_cursor=day_cursor.fromordinal(day_cursor.toordinal()+1)
        by_kind={}
        for kind in ScenarioKind:
            subset=[t for t in trades if t.kind is kind]
            by_kind[kind.value]={"trades":len(subset),"wins":sum(t.net_pnl>0 for t in subset),
                                 "net_pnl":sum(t.net_pnl for t in subset),
                                 "mean_net_r":sum(t.net_r for t in subset)/len(subset) if subset else 0.0}
        daily_growth=(final/self.config.initial_nav)**(1/days)-1
        gross_profit=sum(t.net_pnl for t in wins);gross_loss=abs(sum(t.net_pnl for t in losses))
        return {"initial_nav":self.config.initial_nav,"final_nav":final,"net_return":final/self.config.initial_nav-1,
                "daily_log_growth":log(final/self.config.initial_nav)/days,"daily_geometric_growth":daily_growth,
                "target_daily_geometric_growth":0.01,"target_met":daily_growth>=0.01,"days":days,
                "trades":len(trades),"trades_per_day":len(trades)/days,"wins":len(wins),"losses":len(losses),
                "win_rate":len(wins)/len(trades) if trades else 0.0,
                "mean_net_r":sum(t.net_r for t in trades)/len(trades) if trades else 0.0,
                "median_net_r":median([t.net_r for t in trades]) if trades else 0.0,
                "profit_factor":gross_profit/gross_loss if gross_loss>0 else (float('inf') if gross_profit>0 else 0.0),
                "max_drawdown":max_dd,"by_scenario":by_kind,"daily_returns":daily_returns,
                "cost_assumptions":{"taker_fee_bps_each_fill":self.config.taker_fee_bps,
                "slippage_bps_each_fill":self.config.slippage_bps,"funding_bps_per_8h":self.config.funding_bps_per_8h},
                "risk_fraction":self.config.risk_fraction,"single_slot_enforced":True,
                "trades_detail":[asdict(t) for t in trades]}
