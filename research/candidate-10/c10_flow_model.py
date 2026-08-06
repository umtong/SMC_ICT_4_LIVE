"""Contracts for candidate 10 v3 event-notional flow auctions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class FlowTickView:
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
        return abs(self.net_move) / self.path_travel if self.path_travel > 0.0 else 0.0

    @property
    def range_width(self) -> float:
        return self.high - self.low


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
    initial_flow_threshold: float
    initial_bar_close: float
    outside_closes: int


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
    # Event bars close after one quarter of the rolling median one-minute
    # notional. This adapts bar duration to activity rather than optimizing a
    # clock interval for BTC.
    minute_notional_lookback: int = 60
    minimum_minute_history: int = 30
    event_notional_fraction: float = 0.25

    # The preceding 20 complete event bars form the active local dealing range.
    # At median activity this is approximately five minutes, while event time
    # naturally accelerates or slows with trading intensity.
    range_event_bars: int = 20
    feature_lookback: int = 240
    minimum_feature_history: int = 80

    # Flow thresholds are causal rolling quantiles, not asset-specific absolute
    # volume constants.
    flow_extreme_quantile: float = 0.75
    flow_reversal_quantile: float = 0.50
    enable_order_flow: bool = True

    raid_atr: float = 0.15
    probe_max_bars: int = 2
    stop_buffer_atr: float = 1.00
    entry_expiry_bars: int = 8
    min_net_rr: float = 1.35

    maker_fee: float = 0.000400
    taker_fee: float = 0.000700
    execution_reserve_ticks: int = 2
