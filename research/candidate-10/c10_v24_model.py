"""Causal data contracts for candidate-10 v24 cross-market reconciliation."""
from __future__ import annotations

from dataclasses import dataclass, field
from math import log


NS_PER_SECOND = 1_000_000_000
NS_PER_MINUTE = 60 * NS_PER_SECOND


@dataclass(frozen=True, slots=True)
class CrossMarketParams:
    """Fixed structural parameters for the first v24 experiment.

    Thresholds are dimensionless robust deviations or state durations. They are
    fixed before the first evaluated week and are not symbol/session exceptions.
    """

    bar_seconds: int = 5
    feature_lookback: int = 720  # one hour of completed five-second rows
    minimum_feature_history: int = 360  # thirty minutes before detection
    return_horizon_bars: int = 6  # thirty-second price discovery horizon
    dislocation_z: float = 2.5
    basis_z: float = 2.0
    lag_ratio: float = 0.65
    basis_contraction_fraction: float = 0.20
    probe_max_bars: int = 12  # one minute for reconciliation
    cooldown_normal_bars: int = 3
    cooldown_basis_z: float = 0.75
    cooldown_return_z: float = 1.0
    stop_range_multiple: float = 1.0
    impact_range_fraction: float = 0.15
    current_range_impact_fraction: float = 0.10
    min_net_rr: float = 1.35
    taker_fee: float = 0.0007
    execution_reserve_ticks: int = 2
    use_spot_flow: bool = True


@dataclass(frozen=True, slots=True)
class CrossMarketBar:
    """A completed, aligned spot/perpetual five-second auction observation."""

    ts_ns: int
    spot_open: float
    spot_high: float
    spot_low: float
    spot_close: float
    spot_quote_volume: float
    spot_taker_buy_quote: float
    spot_trade_count: int
    perp_open: float
    perp_high: float
    perp_low: float
    perp_close: float
    perp_quote_volume: float
    perp_taker_buy_quote: float
    perp_trade_count: int

    @staticmethod
    def _flow(total: float, taker_buy: float) -> float:
        if total <= 0.0:
            return 0.0
        return (2.0 * taker_buy - total) / total

    @property
    def spot_flow(self) -> float:
        return self._flow(self.spot_quote_volume, self.spot_taker_buy_quote)

    @property
    def perp_flow(self) -> float:
        return self._flow(self.perp_quote_volume, self.perp_taker_buy_quote)

    @property
    def perp_range(self) -> float:
        return max(0.0, self.perp_high - self.perp_low)

    @property
    def basis_log(self) -> float:
        if self.spot_close <= 0.0 or self.perp_close <= 0.0:
            raise ValueError("spot and perpetual closes must be positive")
        return log(self.perp_close / self.spot_close)


@dataclass(slots=True)
class CrossMarketProbe:
    scenario_id: str
    mode: str  # SPOT_LEAD_CATCHUP or PERP_OVERSHOOT_REVERSION
    source_id: str
    target_id: str
    initiated_sequence: int
    initiated_ns: int
    trade_direction: int
    move_direction: int
    fair_basis: float
    fair_target: float
    initial_basis_deviation: float
    initial_basis_abs_z: float
    spot_event_price: float
    perp_event_price: float
    spot_event_return: float
    perp_event_return: float
    perp_extreme_high: float
    perp_extreme_low: float


@dataclass(frozen=True, slots=True)
class CrossMarketPlan:
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
class CrossMarketTransition:
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
    "CrossMarketBar",
    "CrossMarketParams",
    "CrossMarketPlan",
    "CrossMarketProbe",
    "CrossMarketTransition",
    "NS_PER_MINUTE",
    "NS_PER_SECOND",
]
