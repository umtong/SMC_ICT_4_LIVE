"""Causal contracts for candidate 10 v3 event-notional flow auctions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class FlowTickView:
    """Minimal executed-trade view known at ``ts_ns``."""

    ts_ns: int
    price: float
    quantity: float
    aggressor: int  # +1 buyer initiated, -1 seller initiated
    trade_id: str

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass(slots=True)
class FlowBar:
    """Atomic aggregate-trade bucket closed by a causal notional threshold."""

    sequence: int
    start_ns: int
    end_ns: int
    threshold_notional: float
    open: float
    high: float
    low: float
    close: float
    quantity: float
    notional: float
    buyer_notional: float
    seller_notional: float
    path_travel: float
    tick_count: int
    previous_tick_price: float

    @classmethod
    def from_tick(
        cls,
        *,
        sequence: int,
        threshold_notional: float,
        tick: FlowTickView,
    ) -> "FlowBar":
        buyer = tick.notional if tick.aggressor > 0 else 0.0
        seller = tick.notional if tick.aggressor < 0 else 0.0
        return cls(
            sequence=sequence,
            start_ns=tick.ts_ns,
            end_ns=tick.ts_ns,
            threshold_notional=threshold_notional,
            open=tick.price,
            high=tick.price,
            low=tick.price,
            close=tick.price,
            quantity=tick.quantity,
            notional=tick.notional,
            buyer_notional=buyer,
            seller_notional=seller,
            path_travel=0.0,
            tick_count=1,
            previous_tick_price=tick.price,
        )

    def update(self, tick: FlowTickView) -> None:
        self.path_travel += abs(tick.price - self.previous_tick_price)
        self.previous_tick_price = tick.price
        self.end_ns = tick.ts_ns
        self.high = max(self.high, tick.price)
        self.low = min(self.low, tick.price)
        self.close = tick.price
        self.quantity += tick.quantity
        self.notional += tick.notional
        if tick.aggressor > 0:
            self.buyer_notional += tick.notional
        elif tick.aggressor < 0:
            self.seller_notional += tick.notional
        self.tick_count += 1

    @property
    def signed_notional(self) -> float:
        return self.buyer_notional - self.seller_notional

    @property
    def delta_ratio(self) -> float:
        return self.signed_notional / self.notional if self.notional > 0.0 else 0.0

    @property
    def net_move(self) -> float:
        return self.close - self.open

    @property
    def efficiency(self) -> float:
        """Net price progress divided by total executed-price path travel."""

        if self.path_travel <= 0.0:
            return 0.0
        return abs(self.net_move) / self.path_travel

    @property
    def range_width(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        if self.range_width <= 0.0:
            return 0.5
        return (self.close - self.low) / self.range_width

    def true_range(self, previous_close: float | None) -> float:
        if previous_close is None:
            return self.range_width
        return max(
            self.range_width,
            abs(self.high - previous_close),
            abs(self.low - previous_close),
        )


@dataclass(slots=True)
class FlowRaidProbe:
    scenario_id: str
    direction: int  # reversal direction
    source_side: str
    boundary: float
    opposite_boundary: float
    raid_extreme: float
    initiated_sequence: int
    initiated_ns: int
    initial_delta_ratio: float
    initial_efficiency: float
    initial_flow_threshold: float
    initial_bar_open: float
    initial_bar_close: float


@dataclass(frozen=True, slots=True)
class FlowTradePlan:
    scenario_id: str
    scenario: str
    direction: int
    observed_ns: int
    entry_price: float
    stop_price: float
    target_price: float
    source_boundary: float
    opposite_boundary: float
    event_atr: float
    entry_expiry_bars: int
    invalidation_price: float
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FlowTransition:
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
class FlowParams:
    # Event bars close after one quarter of the rolling median completed-minute
    # notional. This uses event time rather than fitting a BTC-specific clock bar.
    minute_notional_lookback: int = 60
    minimum_minute_history: int = 30
    event_notional_fraction: float = 0.25

    # The preceding complete event bars define the local dealing range and all
    # empirical thresholds. Current-bar values never enter their own thresholds.
    range_event_bars: int = 20
    feature_lookback: int = 240
    minimum_feature_history: int = 80
    atr_event_bars: int = 80
    minimum_atr_history: int = 40

    # Order-flow and price-response thresholds are causal rolling quantiles.
    flow_extreme_quantile: float = 0.75
    flow_reversal_quantile: float = 0.50
    absorption_efficiency_quantile: float = 0.50
    repricing_efficiency_quantile: float = 0.50
    minimum_delta_ratio: float = 0.08
    minimum_efficiency: float = 0.15
    enable_order_flow: bool = True

    # Scenario grammar: raid -> failed price response (absorption) -> opposite
    # efficient repricing -> first passive retrace toward the repricing origin.
    raid_atr: float = 0.15
    repricing_atr: float = 0.35
    probe_max_bars: int = 3
    retrace_fraction: float = 0.50
    stop_buffer_atr: float = 1.00
    cost_floor_multiple: float = 1.00
    entry_expiry_bars: int = 8
    min_net_rr: float = 1.35

    maker_fee: float = 0.000400
    taker_fee: float = 0.000700
    execution_reserve_ticks: int = 2
