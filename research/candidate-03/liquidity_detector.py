"""Causal liquidity-pool and first-breach detector."""
from __future__ import annotations
from collections import deque
from hashlib import sha256
from statistics import median
from typing import Any
from model import Bar,Direction,LiquidityPool,PoolKind,PoolSide,ScenarioKind,SweepObservation,StrategyConfig
from strategy_common import Emit,NS_PER_MINUTE,close_location,true_range,utc_date_key

class LiquidityDetector:
    """Builds causal liquidity pools and consumes each on first material breach."""
    def __init__(self,config:StrategyConfig,emit:Emit)->None:
        self.config=config; self.emit=emit
        self.active_pools:dict[str,LiquidityPool]={}
        self.bars:list[Bar]=[]; self.atr_values:list[float]=[]
        self._true_ranges:deque[float]=deque(maxlen=config.atr_window)
        self._five_bars:list[Bar]=[]; self._five_tr:deque[float]=deque(maxlen=config.five_minute_atr_window)
        self._five_bucket:int|None=None; self._five_components:list[Bar]=[]
        self._range_history:deque[float]=deque(maxlen=config.range_history_count); self._last_range_boundary:int|None=None
        self._day_bars:list[Bar]=[]; self._day_key:str|None=None
    @property
    def atr(self)->float|None:
        return sum(self._true_ranges)/len(self._true_ranges) if len(self._true_ranges)==self.config.atr_window else None
    def on_bar(self,bar:Bar,index:int)->list[SweepObservation]:
        previous=self.bars[-1].close if self.bars else None
        self._true_ranges.append(true_range(bar,previous)); self.bars.append(bar)
        atr=self.atr; self.atr_values.append(atr or 0.0)
        self._update_day(bar,atr); self._update_five(bar,atr); self._update_range(bar,atr); self._expire(bar.close_time_ns)
        if atr is None or index<self.config.micro_lookback: return []
        output:list[SweepObservation]=[]
        for pool in sorted(self.active_pools.values(),key=lambda p:(p.created_time_ns,p.pool_id)):
            if not pool.active or pool.observed_time_ns>=bar.close_time_ns: continue
            sweep=self._sweep(pool,bar,index,atr)
            if sweep is None: continue
            pool.consumed_time_ns=bar.close_time_ns
            self.emit(scenario_id=f"POOL::{pool.pool_id}",event_type="LIQUIDITY_SWEEP",
                      event_time_ns=bar.close_time_ns,observed_time_ns=bar.close_time_ns,
                      previous_state="ACTIVE",next_state="CONSUMED",reason_code="FIRST_MATERIAL_BREACH",
                      reference_price=pool.price,details={"pool_id":pool.pool_id,"pool_kind":pool.kind.value,
                      "pool_side":pool.side.value,"penetration_atr":sweep.penetration_atr,
                      "extreme_price":sweep.extreme_price,"initial_bias":sweep.initial_bias.value if sweep.initial_bias else None})
            output.append(sweep)
        return output
    def targets(self,direction:Direction,entry:float,observed_ns:int)->list[float]:
        prices=[]
        for pool in self.active_pools.values():
            if not pool.active or pool.observed_time_ns>observed_ns: continue
            if direction is Direction.LONG and pool.side is PoolSide.HIGH and pool.price>entry: prices.append(pool.price)
            if direction is Direction.SHORT and pool.side is PoolSide.LOW and pool.price<entry: prices.append(pool.price)
        return sorted(set(prices),reverse=direction is Direction.SHORT)
    def _id(self,kind:PoolKind,side:PoolSide,price:float,observed_ns:int)->str:
        return sha256(f"{kind.value}|{side.value}|{price:.8f}|{observed_ns}".encode()).hexdigest()[:20]
    def _add_pair(self,*,kind:PoolKind,high:float,low:float,event_ns:int,observed_ns:int,expires_ns:int,
                  atr:float|None,details:dict[str,Any])->None:
        if high<=low:return
        self._add(side=PoolSide.HIGH,kind=kind,price=high,counterpart=low,event_ns=event_ns,observed_ns=observed_ns,
                  expires_ns=expires_ns,atr=atr,details=details)
        self._add(side=PoolSide.LOW,kind=kind,price=low,counterpart=high,event_ns=event_ns,observed_ns=observed_ns,
                  expires_ns=expires_ns,atr=atr,details=details)
    def _add(self,*,side:PoolSide,kind:PoolKind,price:float,counterpart:float|None,event_ns:int,observed_ns:int,
             expires_ns:int,atr:float|None,details:dict[str,Any])->None:
        if atr and atr>0:
            distance=self.config.pool_merge_atr*atr
            for existing in self.active_pools.values():
                if existing.active and existing.side is side and abs(existing.price-price)<=distance:
                    existing.expires_time_ns=max(existing.expires_time_ns,expires_ns)
                    existing.details.setdefault("confluence",[]).append({"kind":kind.value,**details}); return
        pool_id=self._id(kind,side,price,observed_ns)
        pool=LiquidityPool(pool_id,side,kind,price,event_ns,observed_ns,expires_ns,counterpart_price=counterpart,details=dict(details))
        self.active_pools[pool_id]=pool
        self.emit(scenario_id=f"POOL::{pool_id}",event_type="LIQUIDITY_POOL_CREATED",event_time_ns=event_ns,
                  observed_time_ns=observed_ns,previous_state="NONE",next_state="ACTIVE",reason_code=kind.value,
                  reference_price=price,details={"pool_id":pool_id,"pool_kind":kind.value,"pool_side":side.value,
                  "counterpart_price":counterpart,**details})
    def _update_day(self,bar:Bar,atr:float|None)->None:
        key=utc_date_key(bar.open_time_ns)
        if self._day_key is None:self._day_key=key
        elif key!=self._day_key:
            previous=self._day_bars
            if previous:
                observed=bar.close_time_ns
                self._add_pair(kind=PoolKind.PREVIOUS_DAY,high=max(b.high for b in previous),low=min(b.low for b in previous),
                               event_ns=previous[0].open_time_ns,observed_ns=observed,
                               expires_ns=observed+self.config.previous_day_pool_ttl_minutes*NS_PER_MINUTE,atr=atr,
                               details={"utc_day":self._day_key})
            self._day_bars=[]; self._day_key=key
        self._day_bars.append(bar)
    @staticmethod
    def _aggregate(items:list[Bar])->Bar:
        return Bar(items[0].open_time_ns,items[-1].close_time_ns,items[0].open,max(b.high for b in items),
                   min(b.low for b in items),items[-1].close,sum(b.volume for b in items),sum(b.quote_volume for b in items),
                   sum(b.trade_count for b in items),sum(b.taker_buy_volume for b in items))
    def _update_five(self,bar:Bar,atr:float|None)->None:
        bucket=bar.open_time_ns//(5*NS_PER_MINUTE)
        if self._five_bucket is None:self._five_bucket=bucket
        if bucket!=self._five_bucket:
            completed=self._aggregate(self._five_components)
            previous=self._five_bars[-1].close if self._five_bars else None
            self._five_tr.append(true_range(completed,previous)); self._five_bars.append(completed)
            self._confirm_pivot(atr,bar.close_time_ns)
            self._five_components=[]; self._five_bucket=bucket
        self._five_components.append(bar)
    def _confirm_pivot(self,one_atr:float|None,observed_ns:int)->None:
        left=self.config.swing_left; right=self.config.swing_right; required=left+right+1
        if len(self._five_bars)<required or len(self._five_tr)<self.config.five_minute_atr_window:return
        pivot_i=len(self._five_bars)-right-1; pivot=self._five_bars[pivot_i]
        neighborhood=self._five_bars[pivot_i-left:pivot_i+right+1]; others=[b for j,b in enumerate(neighborhood) if j!=left]
        atr5=sum(self._five_tr)/len(self._five_tr); expires=observed_ns+self.config.swing_pool_ttl_minutes*NS_PER_MINUTE
        high_prom=pivot.high-max(b.high for b in others); low_prom=min(b.low for b in others)-pivot.low
        if pivot.high==max(b.high for b in neighborhood) and high_prom>=self.config.swing_prominence_atr*atr5:
            self._add(side=PoolSide.HIGH,kind=PoolKind.CONFIRMED_SWING,price=pivot.high,counterpart=None,
                      event_ns=pivot.close_time_ns,observed_ns=observed_ns,expires_ns=expires,atr=one_atr,
                      details={"pivot_time_ns":pivot.close_time_ns,"confirmation_delay_bars":right})
        if pivot.low==min(b.low for b in neighborhood) and low_prom>=self.config.swing_prominence_atr*atr5:
            self._add(side=PoolSide.LOW,kind=PoolKind.CONFIRMED_SWING,price=pivot.low,counterpart=None,
                      event_ns=pivot.close_time_ns,observed_ns=observed_ns,expires_ns=expires,atr=one_atr,
                      details={"pivot_time_ns":pivot.close_time_ns,"confirmation_delay_bars":right})
    def _update_range(self,bar:Bar,atr:float|None)->None:
        boundary=bar.close_time_ns//(self.config.range_stride_minutes*NS_PER_MINUTE)
        if self._last_range_boundary is None:self._last_range_boundary=boundary;return
        if boundary==self._last_range_boundary:return
        self._last_range_boundary=boundary; window=self.config.range_window_minutes
        if len(self.bars)<window:return
        items=self.bars[-window:]; high=max(b.high for b in items); low=min(b.low for b in items); width=high-low
        if len(self._range_history)>=max(4,self.config.range_history_count//2):
            reference=median(self._range_history)
            if reference>0 and width<=self.config.range_compression_ratio*reference:
                observed=bar.close_time_ns
                self._add_pair(kind=PoolKind.DEALING_RANGE,high=high,low=low,event_ns=items[0].open_time_ns,
                               observed_ns=observed,expires_ns=observed+self.config.range_pool_ttl_minutes*NS_PER_MINUTE,
                               atr=atr,details={"window_minutes":window,"range":width,"reference_range":reference,
                               "compression_ratio":width/reference})
        self._range_history.append(width)
    def _expire(self,observed_ns:int)->None:
        for pool in self.active_pools.values():
            if pool.active and observed_ns>pool.expires_time_ns:
                pool.consumed_time_ns=observed_ns
                self.emit(scenario_id=f"POOL::{pool.pool_id}",event_type="LIQUIDITY_POOL_EXPIRED",
                          event_time_ns=observed_ns,observed_time_ns=observed_ns,previous_state="ACTIVE",next_state="EXPIRED",
                          reason_code="TTL_WITHOUT_MATERIAL_BREACH",reference_price=pool.price,details={"pool_id":pool.pool_id})
    def _sweep(self,pool:LiquidityPool,bar:Bar,index:int,atr:float)->SweepObservation|None:
        prior=self.bars[index-self.config.micro_lookback:index]
        if pool.side is PoolSide.HIGH:
            penetration=bar.high-pool.price
            if not self.config.min_sweep_atr*atr<=penetration<=self.config.max_sweep_atr*atr:return None
            bias=ScenarioKind.REJECTION if bar.close<pool.price else (ScenarioKind.ACCEPTANCE if bar.close>=pool.price+self.config.accept_close_atr*atr else None)
            pre_break=min(b.low for b in prior); extreme=bar.high
        else:
            penetration=pool.price-bar.low
            if not self.config.min_sweep_atr*atr<=penetration<=self.config.max_sweep_atr*atr:return None
            bias=ScenarioKind.REJECTION if bar.close>pool.price else (ScenarioKind.ACCEPTANCE if bar.close<=pool.price-self.config.accept_close_atr*atr else None)
            pre_break=max(b.high for b in prior); extreme=bar.low
        return SweepObservation(pool,index,bar.close_time_ns,extreme,penetration/atr,close_location(bar),pre_break,atr,bias)
