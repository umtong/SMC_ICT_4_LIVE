"""Causal contracts for candidate-10 v25 liquidity-response auctions."""
from __future__ import annotations

from dataclasses import dataclass, field

NS_PER_SECOND = 1_000_000_000
NS_PER_MINUTE = 60 * NS_PER_SECOND


@dataclass(frozen=True, slots=True)
class LiquidityResponseParams:
    """Structural controls fixed before the first evaluated BTC week."""

    bar_seconds: int = 1
    formation_seconds: int = 30
    feature_lookback_windows: int = 240  # two hours
    minimum_feature_windows: int = 60  # thirty minutes
    formation_flow_quantile: float = 0.75
    formation_efficiency_quantile: float = 0.35
    formation_dominance_quantile: float = 0.60
    interaction_flow_quantile: float = 0.75
    confirmation_flow_quantile: float = 0.50
    quote_ofi_quantile: float = 0.50
    replenishment_ratio: float = 1.0
    probe_max_bars: int = 20
    approach_bars: int = 5
    shelf_zone_range_fraction: float = 0.15
    shelf_zone_spread_multiple: float = 2.0
    stop_range_multiple: float = 1.0
    impact_range_fraction: float = 0.15
    current_range_impact_fraction: float = 0.10
    min_net_rr: float = 1.35
    taker_fee: float = 0.0007
    execution_reserve_ticks: int = 2
    max_shelves: int = 96
    use_quote_response: bool = True


@dataclass(frozen=True, slots=True)
class LiquidityResponseBar:
    """One completed second of top-of-book and executed order flow."""

    ts_ns: int
    mid_open: float
    mid_high: float
    mid_low: float
    mid_close: float
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    mean_spread: float
    max_spread: float
    quote_updates: int
    ofi_qty: float
    bid_add_qty: float
    bid_remove_qty: float
    ask_add_qty: float
    ask_remove_qty: float
    trade_quote_volume: float
    taker_buy_quote: float
    trade_base_volume: float
    taker_buy_base: float
    trade_count: int

    @property
    def spread(self) -> float:
        return max(0.0, self.ask_price - self.bid_price)

    @property
    def mid_range(self) -> float:
        return max(0.0, self.mid_high - self.mid_low)

    @property
    def signed_trade_quote(self) -> float:
        return 2.0 * self.taker_buy_quote - self.trade_quote_volume

    @property
    def signed_trade_base(self) -> float:
        return 2.0 * self.taker_buy_base - self.trade_base_volume

    @property
    def trade_flow(self) -> float:
        if self.trade_quote_volume <= 0.0:
            return 0.0
        return self.signed_trade_quote / self.trade_quote_volume

    @property
    def top_depth(self) -> float:
        return max(0.0, self.bid_size + self.ask_size)


@dataclass(slots=True)
class LiquidityShelf:
    shelf_id: str
    side: int  # +1 supply formed under buy pressure, -1 demand under sell pressure
    price: float
    zone: float
    created_ns: int
    formation_start_ns: int
    formation_end_ns: int
    flow_dominance: float
    impact_efficiency: float
    active: bool = True
    reserved: bool = False


@dataclass(slots=True)
class LiquidityProbe:
    scenario_id: str
    source_ids: tuple[str, ...]
    source_side: int
    source_price: float
    source_zone: float
    initiated_sequence: int
    initiated_ns: int
    move_direction: int
    trade_direction: int
    raid_high: float
    raid_low: float
    approach_low: float
    approach_high: float
    target_id: str
    target_price: float
    cumulative_attacked_add: float = 0.0
    cumulative_attacked_remove: float = 0.0
    cumulative_aggressive_base: float = 0.0
    reclaim_seen: bool = False


@dataclass(frozen=True, slots=True)
class LiquidityResponsePlan:
    scenario_id: str
    scenario: str
    direction: int
    observed_ns: int
    entry_estimate: float
    stop_price: float
    target_price: float
    source_pool_id: str
    target_pool_id: str
    expected_entry_impact: float
    expected_stop_impact: float
    cost_adjusted_net_rr: float
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class LiquidityResponseTransition:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None
    details: dict[str, object] = field(default_factory=dict)


__all__ = [
    "LiquidityProbe",
    "LiquidityResponseBar",
    "LiquidityResponseParams",
    "LiquidityResponsePlan",
    "LiquidityResponseTransition",
    "LiquidityShelf",
    "NS_PER_MINUTE",
    "NS_PER_SECOND",
]
