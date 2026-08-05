"""Domain model for candidate-03's causal liquidity-auction strategy."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any

class Direction(StrEnum):
    LONG = "LONG"; SHORT = "SHORT"
    @property
    def sign(self) -> int: return 1 if self is Direction.LONG else -1
class PoolSide(StrEnum): HIGH = "HIGH"; LOW = "LOW"
class PoolKind(StrEnum):
    CONFIRMED_SWING = "CONFIRMED_SWING"; DEALING_RANGE = "DEALING_RANGE"; PREVIOUS_DAY = "PREVIOUS_DAY"
class ScenarioKind(StrEnum): REJECTION = "REJECTION"; ACCEPTANCE = "ACCEPTANCE"
class ScenarioState(StrEnum):
    IDLE = "IDLE"; CLASSIFYING = "CLASSIFYING"; ENTRY_ARMED = "ENTRY_ARMED"; POSITION_ACTIVE = "POSITION_ACTIVE"
    CLOSED = "CLOSED"; INVALIDATED = "INVALIDATED"; EXPIRED = "EXPIRED"
class ExitReason(StrEnum):
    TARGET = "TARGET"; STOP = "STOP"; CAUSAL_INVALIDATION = "CAUSAL_INVALIDATION"
    OPPORTUNITY_EXPIRED = "OPPORTUNITY_EXPIRED"; END_OF_RUN = "END_OF_RUN"

@dataclass(frozen=True, slots=True)
class Bar:
    open_time_ns: int; close_time_ns: int
    open: float; high: float; low: float; close: float
    volume: float; quote_volume: float; trade_count: int; taker_buy_volume: float
    def __post_init__(self) -> None:
        if self.open_time_ns < 0 or self.close_time_ns <= self.open_time_ns: raise ValueError("bar timestamps must be ordered")
        values=(self.open,self.high,self.low,self.close,self.volume,self.quote_volume,self.taker_buy_volume)
        if not all(isfinite(v) for v in values): raise ValueError("bar values must be finite")
        if self.low > min(self.open,self.close) or self.high < max(self.open,self.close) or self.low > self.high: raise ValueError("invalid OHLC")
        if self.volume < 0 or self.quote_volume < 0 or self.trade_count < 0: raise ValueError("negative activity")
        if self.taker_buy_volume < 0 or self.taker_buy_volume > self.volume + 1e-9: raise ValueError("invalid taker volume")
    @property
    def range(self)->float: return self.high-self.low
    @property
    def body(self)->float: return abs(self.close-self.open)
    @property
    def signed_volume(self)->float: return 2.0*self.taker_buy_volume-self.volume

@dataclass(slots=True)
class LiquidityPool:
    pool_id:str; side:PoolSide; kind:PoolKind; price:float
    created_time_ns:int; observed_time_ns:int; expires_time_ns:int
    counterpart_price:float|None=None; consumed_time_ns:int|None=None; details:dict[str,Any]=field(default_factory=dict)
    @property
    def active(self)->bool: return self.consumed_time_ns is None

@dataclass(slots=True)
class SweepObservation:
    pool:LiquidityPool; bar_index:int; observed_time_ns:int; extreme_price:float
    penetration_atr:float; close_location:float; pre_break_price:float; atr:float; initial_bias:ScenarioKind|None
    @property
    def rejection_direction(self)->Direction: return Direction.SHORT if self.pool.side is PoolSide.HIGH else Direction.LONG
    @property
    def acceptance_direction(self)->Direction: return Direction.LONG if self.pool.side is PoolSide.HIGH else Direction.SHORT

@dataclass(slots=True)
class Scenario:
    scenario_id:str; sweep:SweepObservation; state:ScenarioState; created_index:int; last_index:int
    kind:ScenarioKind|None=None; direction:Direction|None=None; evidence_bars:list[Bar]=field(default_factory=list)
    displacement_index:int|None=None; entry_price:float|None=None; stop_price:float|None=None; target_price:float|None=None
    armed_index:int|None=None; reason:str=""

@dataclass(frozen=True, slots=True)
class EntryPlan:
    scenario_id:str; kind:ScenarioKind; direction:Direction; entry_price:float; stop_price:float; target_price:float
    armed_index:int; expires_index:int; atr:float; pool_id:str; evidence:dict[str,Any]

@dataclass(slots=True)
class Position:
    scenario_id:str; kind:ScenarioKind; direction:Direction; entry_time_ns:int; entry_index:int
    entry_price:float; stop_price:float; target_price:float; quantity:float; nav_before:float; planned_loss:float
    entry_cost:float; max_favorable_price:float; max_adverse_price:float; pool_id:str

@dataclass(frozen=True, slots=True)
class Trade:
    scenario_id:str; kind:ScenarioKind; direction:Direction; pool_id:str; entry_time_ns:int; exit_time_ns:int
    entry_price:float; exit_price:float; stop_price:float; target_price:float; quantity:float
    nav_before:float; nav_after:float; net_pnl:float; net_r:float; holding_minutes:float
    exit_reason:ExitReason; mfe_r:float; mae_r:float

@dataclass(frozen=True, slots=True)
class StrategyConfig:
    candidate:str="candidate-03"; instrument_id:str="BTCUSDT-PERP.BINANCE"
    initial_nav:float=100_000.0; risk_fraction:float=0.03
    taker_fee_bps:float=5.0; slippage_bps:float=1.5; funding_bps_per_8h:float=1.0
    atr_window:int=60; five_minute_atr_window:int=24; swing_left:int=2; swing_right:int=2
    swing_prominence_atr:float=0.35; swing_pool_ttl_minutes:int=720
    range_window_minutes:int=60; range_stride_minutes:int=60; range_history_count:int=12
    range_compression_ratio:float=1.0; range_pool_ttl_minutes:int=480; previous_day_pool_ttl_minutes:int=1440
    pool_merge_atr:float=0.08; min_sweep_atr:float=0.04; max_sweep_atr:float=1.50; accept_close_atr:float=0.08
    micro_lookback:int=4; confirm_bars:int=5; displacement_atr:float=0.70
    displacement_body_fraction:float=0.55; displacement_close_fraction:float=0.28; flow_imbalance_threshold:float=0.05
    acceptance_hold_bars:int=2; entry_retrace_fraction:float=0.50; entry_wait_bars:int=8
    stop_buffer_atr:float=0.12; min_net_reward_risk:float=1.35; max_holding_bars:int=180
    invalidation_close_atr:float=0.18; target_buffer_atr:float=0.03; warmup_minutes:int=2880
    def validate(self)->None:
        if not 0 < self.risk_fraction <= 0.03: raise ValueError("risk_fraction must be in (0, 0.03]")
        if self.initial_nav <= 0: raise ValueError("initial_nav must be positive")
        if min(self.taker_fee_bps,self.slippage_bps,self.funding_bps_per_8h)<0: raise ValueError("costs cannot be negative")
        ints=(self.atr_window,self.five_minute_atr_window,self.swing_left,self.swing_right,self.range_window_minutes,
              self.range_stride_minutes,self.range_history_count,self.confirm_bars,self.entry_wait_bars,self.max_holding_bars,self.warmup_minutes)
        if any(v<=0 for v in ints): raise ValueError("windows must be positive")
        if not 0 < self.entry_retrace_fraction < 1: raise ValueError("entry retrace must be between zero and one")
