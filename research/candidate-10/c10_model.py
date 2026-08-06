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
class StructuralBar:
    bucket_id: int
    start_ns: int
    end_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    minute_count: int = 1

    def update(self, bar: BarView) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.volume += bar.volume
        self.end_ns = bar.ts_ns
        self.minute_count += 1


@dataclass(slots=True)
class LiquidityPool:
    pool_id: str
    side: str
    center: float
    lower: float
    upper: float
    event_time_ns: int
    observed_time_ns: int
    last_source_time_ns: int
    source_count: int
    max_prominence_atr: float
    status: str
    outside_closes: int = 0
    touch_count: int = 0
    consumed_time_ns: int | None = None
    consumed_reason: str | None = None


@dataclass(slots=True)
class Setup:
    scenario_id: str
    scenario: str
    direction: int
    source_pool_id: str
    source_pool_side: str
    source_lower: float
    source_upper: float
    state: str
    created_index: int
    created_ns: int
    atr: float
    raid_extreme: float
    approach_level: float
    path_last_close: float
    path_travel: float
    path_bars: int
    confirmation_index: int | None = None
    zone_low: float | None = None
    zone_high: float | None = None
    stop_price: float | None = None


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
    # Pools are confirmed only after two complete 15-minute bars exist on each
    # side of the candidate pivot. Numerical values are scale-normalized by ATR.
    structure_minutes: int = 15
    pivot_left: int = 2
    pivot_right: int = 2
    structural_atr_lookback: int = 20
    single_swing_prominence_atr: float = 0.90
    pool_zone_atr: float = 0.08
    pool_merge_atr: float = 0.15
    cluster_min_sources: int = 2
    enable_pool_clustering: bool = True
    pool_max_age_minutes: int = 4_320

    atr_lookback: int = 60
    approach_lookback: int = 8
    raid_atr: float = 0.08
    acceptance_atr: float = 0.12

    # Displacement is an efficient event-time path, not one candle. The path
    # must move away from the raid extreme, break approach structure, and avoid
    # spending most of its travel oscillating in both directions.
    displacement_atr: float = 0.75
    displacement_max_bars: int = 10
    displacement_min_efficiency: float = 0.55
    displacement_speed_atr: float = 0.32

    # Executable entry/invalidation grammar retained from v1.
    stop_buffer_atr: float = 1.00
    cost_floor_multiple: float = 1.00
    maker_fee: float = 0.000400
    taker_fee: float = 0.000700
    execution_reserve_ticks: int = 2
    rejection_limit_fraction: float = 0.618
    retrace_expiry_bars: int = 16
    min_net_rr: float = 1.35
