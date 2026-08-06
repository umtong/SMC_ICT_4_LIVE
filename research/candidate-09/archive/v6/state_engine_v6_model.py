"""Shared causal state and risk contracts for candidate-09 v6."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from math import isfinite
from typing import Any, Mapping

MINUTE_NS = 60_000_000_000


@dataclass(frozen=True, slots=True)
class FlowBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    trade_count: int

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if self.ts_ns < 0 or any(not isfinite(value) for value in values):
            raise ValueError("bar contains an invalid timestamp or non-finite value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is inconsistent")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is inconsistent")
        if self.volume < 0.0 or not 0.0 <= self.taker_buy_volume <= self.volume + 1e-9:
            raise ValueError("bar volume is inconsistent")
        if self.trade_count < 0:
            raise ValueError("trade_count must be non-negative")

    @property
    def signed_flow(self) -> float:
        return 2.0 * self.taker_buy_volume - self.volume

    @property
    def flow_imbalance(self) -> float:
        return self.signed_flow / self.volume if self.volume > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class EngineConfig:
    auction_horizons_minutes: tuple[int, ...] = (5, 15, 60, 1440)
    regime_horizon_minutes: int = 240
    atr_period: int = 20
    volume_period: int = 60
    approach_period: int = 15
    maximum_active_levels_per_side: int = 128
    maximum_level_age_minutes: int = 10080
    minimum_breach_atr: float = 0.08
    cluster_tolerance_atr: float = 0.15
    acceptance_buffer_atr: float = 0.08
    acceptance_closes: int = 2
    acceptance_timeout_bars: int = 8
    failure_timeout_bars: int = 18
    failure_retest_timeout_bars: int = 10
    retest_tolerance_atr: float = 0.20
    defended_close_buffer_atr: float = 0.02
    failure_close_buffer_atr: float = 0.06
    stop_buffer_atr: float = 0.12
    minimum_approach_efficiency: float = 0.08
    minimum_approach_flow: float = 0.02
    directional_imbalance: float = 0.08
    minimum_volume_ratio: float = 1.00
    minimum_displacement_atr: float = 0.35
    minimum_excursion_atr: float = 0.22
    minimum_resolution_displacement_atr: float = 0.25
    minimum_net_reward_to_risk: float = 1.20
    composite_cost_per_fill: float = 0.00075
    cooldown_bars: int = 6
    use_flow_confirmation: bool = True
    require_acceptance_confirmation: bool = True
    require_regime_alignment: bool = True
    require_failure_retest: bool = True
    use_opposite_edge_target: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {"baseline", "no-regime", "no-failure-retest", "opposite-edge-target"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        structure = payload["structure"]
        breach = payload["breach"]
        flow = payload["flow"]
        trade = payload["trade"]
        risk = payload["risk"]
        horizons = tuple(int(value) for value in structure["auction_horizons_minutes"])
        if tuple(sorted(set(horizons))) != horizons or not horizons or any(value <= 0 for value in horizons):
            raise ValueError("auction horizons must be unique, positive, and ascending")
        regime_horizon = int(structure["regime_horizon_minutes"])
        if regime_horizon <= 0:
            raise ValueError("regime horizon must be positive")
        return cls(
            auction_horizons_minutes=horizons,
            regime_horizon_minutes=regime_horizon,
            atr_period=int(structure["atr_period"]),
            volume_period=int(structure["volume_period"]),
            approach_period=int(structure["approach_period"]),
            maximum_active_levels_per_side=int(structure["maximum_active_levels_per_side"]),
            maximum_level_age_minutes=int(structure["maximum_level_age_minutes"]),
            minimum_breach_atr=float(breach["minimum_breach_atr"]),
            cluster_tolerance_atr=float(breach["cluster_tolerance_atr"]),
            acceptance_buffer_atr=float(breach["acceptance_buffer_atr"]),
            acceptance_closes=int(breach["acceptance_closes"]),
            acceptance_timeout_bars=int(breach["acceptance_timeout_bars"]),
            failure_timeout_bars=int(breach["failure_timeout_bars"]),
            failure_retest_timeout_bars=int(breach["failure_retest_timeout_bars"]),
            retest_tolerance_atr=float(breach["retest_tolerance_atr"]),
            defended_close_buffer_atr=float(breach["defended_close_buffer_atr"]),
            failure_close_buffer_atr=float(breach["failure_close_buffer_atr"]),
            stop_buffer_atr=float(breach["stop_buffer_atr"]),
            minimum_approach_efficiency=float(flow["minimum_approach_efficiency"]),
            minimum_approach_flow=float(flow["minimum_approach_flow"]),
            directional_imbalance=float(flow["directional_imbalance"]),
            minimum_volume_ratio=float(flow["minimum_volume_ratio"]),
            minimum_displacement_atr=float(flow["minimum_displacement_atr"]),
            minimum_excursion_atr=float(flow["minimum_excursion_atr"]),
            minimum_resolution_displacement_atr=float(flow["minimum_resolution_displacement_atr"]),
            minimum_net_reward_to_risk=float(trade["minimum_net_reward_to_risk"]),
            composite_cost_per_fill=float(risk["composite_taker_cost_per_fill"]),
            cooldown_bars=int(trade["cooldown_bars"]),
            require_regime_alignment=ablation != "no-regime",
            require_failure_retest=ablation != "no-failure-retest",
            use_opposite_edge_target=ablation == "opposite-edge-target",
        )


@dataclass(slots=True)
class AuctionLevel:
    level_id: str
    kind: str
    price: float
    horizon_minutes: int
    range_start_ns: int
    range_end_ns: int
    range_high: float
    range_low: float
    range_midpoint: float
    range_width: float
    observed_index: int
    consumed: bool = False


@dataclass(slots=True)
class RangeBuilder:
    horizon_minutes: int
    block_key: int
    start_ns: int
    end_ns: int
    high: float
    low: float
    close: float
    bars: int = 1


@dataclass(frozen=True, slots=True)
class CompletedRegimeRange:
    start_ns: int
    end_ns: int
    high: float
    low: float
    close: float
    midpoint: float


@dataclass(slots=True)
class PendingResolution:
    scenario_id: str
    level: AuctionLevel
    direction: str
    state: str
    start_index: int
    approach_efficiency: float
    approach_flow: float
    confluence_count: int
    extreme: float
    outside_closes: int = 0
    displacement_seen: bool = False
    directional_flow_seen: bool = False
    max_volume_ratio: float = 0.0
    post_signed_flow: float = 0.0
    post_volume: float = 0.0
    acceptance_index: int | None = None
    failure_index: int | None = None
    failure_high: float | None = None
    failure_low: float | None = None

    @property
    def post_flow_imbalance(self) -> float:
        return self.post_signed_flow / self.post_volume if self.post_volume > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Signal:
    scenario_id: str
    branch: str
    side: str
    observed_time_ns: int
    entry_reference: float
    stop_price: float
    target_price: float
    net_reward_to_risk: float
    reason_code: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EngineResult:
    events: tuple[DiagnosticEvent, ...]
    signal: Signal | None


@dataclass(frozen=True, slots=True)
class RiskSizing:
    quantity: Decimal
    loss_budget: Decimal
    per_unit_expected_loss: Decimal
    planned_loss: Decimal


def risk_based_quantity(
    *,
    nav: Decimal,
    risk_fraction: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    cost_rate_per_fill: Decimal,
    quantity_increment: Decimal,
) -> RiskSizing:
    if nav <= 0 or not Decimal("0") < risk_fraction <= Decimal("0.03"):
        raise ValueError("NAV must be positive and risk_fraction must be in (0, 0.03]")
    if entry_price <= 0 or stop_price <= 0 or quantity_increment <= 0:
        raise ValueError("prices and quantity increment must be positive")
    if cost_rate_per_fill < 0:
        raise ValueError("cost rate cannot be negative")
    budget = nav * risk_fraction
    per_unit = abs(entry_price - stop_price) + entry_price * cost_rate_per_fill + stop_price * cost_rate_per_fill
    if per_unit <= 0:
        raise ValueError("per-unit expected loss must be positive")
    increments = ((budget / per_unit) / quantity_increment).to_integral_value(rounding=ROUND_FLOOR)
    quantity = increments * quantity_increment
    planned = quantity * per_unit
    if quantity <= 0:
        raise ValueError("risk budget is below one exchange quantity increment")
    if planned > budget:
        raise AssertionError("floored sizing exceeded the planned loss budget")
    return RiskSizing(quantity, budget, per_unit, planned)
