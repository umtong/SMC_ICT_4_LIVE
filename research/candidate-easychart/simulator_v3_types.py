"""Shared data types for the candidate-easychart v3 diagnostic."""
from __future__ import annotations

from dataclasses import dataclass

from domain_v3 import ArmedSetup, TradePlan

@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    symbol: str
    tick_size: float
    size_increment: str
    min_quantity: float
    min_notional: float


@dataclass(frozen=True, slots=True)
class MinuteBar:
    symbol: str
    ts_open_ns: int
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    funding_rate: float | None = None


@dataclass(slots=True)
class PendingSetup:
    setup: ArmedSetup
    favorable_extreme: float


@dataclass(slots=True)
class Position:
    plan: TradePlan
    quantity: float
    entry_time_ns: int
    nav_before: float
    entry_fee_and_slippage: float
    planned_account_loss: float
    entry_notional: float


@dataclass(frozen=True, slots=True)
class TradeRecord:
    plan_id: str
    causal_event_id: str
    symbol: str
    family: str
    target_mode: str
    side: int
    signal_time_ns: int
    entry_time_ns: int
    exit_time_ns: int
    entry: float
    exit: float
    stop: float
    target: float
    gross_rr: float
    quantity: float
    outcome: str
    gross_pnl: float
    fees: float
    slippage: float
    funding: float
    net_pnl: float
    gross_r: float
    cost_r: float
    net_r: float
    nav_before: float
    nav_after: float
    planned_account_loss: float
    entry_notional: float
    entry_notional_to_nav: float
    context_bias: str
    source_timeframe_minutes: int
    body_ratio: float
    previous_body: float
    current_body: float
    hold_minutes: int


@dataclass(frozen=True, slots=True)
class EntryCandidate:
    plan: TradePlan
    path_after_entry: tuple[float, ...]
    bar: MinuteBar
    entered_at_open: bool
    path_segment: int

