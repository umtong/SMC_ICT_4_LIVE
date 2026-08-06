"""Shared causal market model for candidate 10."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NS_PER_MINUTE = 60_000_000_000
MS_PER_MINUTE = 60_000


@dataclass(frozen=True, slots=True)
class BarView:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class AuctionRange:
    block_id: int
    start_ns: int
    end_ns: int
    open: float
    high: float
    low: float
    close: float
    bars: int = 1

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def width(self) -> float:
        return self.high - self.low

    def update(self, bar: BarView) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.end_ns = bar.ts_ns
        self.bars += 1


@dataclass(slots=True)
class Setup:
    scenario_id: str
    scenario: str
    direction: int
    boundary: float
    state: str
    created_index: int
    created_ns: int
    atr: float
    raid_extreme: float
    approach_level: float
    consecutive_closes: int = 0
    confirmation_index: int | None = None
    zone_low: float | None = None
    zone_high: float | None = None
    stop_price: float | None = None
    breakout_extreme: float | None = None


@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    scenario: str
    direction: int
    observed_ns: int
    entry_estimate: float
    stop_price: float
    target_price: float
    boundary: float
    atr: float
    structural_target: str
    entry_order_type: str
    entry_expiry_bars: int
    invalidation_price: float
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Transition:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MachineParams:
    block_minutes: int = 240
    atr_lookback: int = 60
    approach_lookback: int = 6
    raid_atr: float = 0.08
    acceptance_atr: float = 0.12
    displacement_atr: float = 0.75
    # v1: invalidation must sit outside both event noise and one complete
    # executable round-trip cost floor, not just a few ticks past a 1-minute wick.
    stop_buffer_atr: float = 1.00
    cost_floor_multiple: float = 1.00
    maker_fee: float = 0.000400
    taker_fee: float = 0.000700
    execution_reserve_ticks: int = 2
    rejection_limit_fraction: float = 0.618
    rejection_confirm_bars: int = 10
    retrace_expiry_bars: int = 16
    acceptance_retest_bars: int = 24
    acceptance_target_extension: float = 0.50
    min_net_rr: float = 1.35
    enable_rejection: bool = True
    enable_acceptance: bool = True
