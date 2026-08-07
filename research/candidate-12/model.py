"""Candidate 12 domain contracts and exact NAV-risk sizing."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum
import math
from typing import Any

NS_MINUTE = 60_000_000_000

NS_DAY = 86_400_000_000_000

class Side(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

class ScenarioKind(str, Enum):
    REJECTION = "REJECTION"
    ACCEPTANCE = "ACCEPTANCE"

@dataclass(frozen=True, slots=True)
class BarObs:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("bar contains a non-finite value")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is inconsistent")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is inconsistent")
        if self.volume < 0 or self.taker_buy_volume < 0:
            raise ValueError("bar volume cannot be negative")
        if self.taker_buy_volume > self.volume + 1e-9:
            raise ValueError("taker-buy volume exceeds total volume")

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))

    @property
    def close_location(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.close - self.low) / self.range

@dataclass(frozen=True, slots=True)
class LogicConfig:
    atr_period: int = 30
    volume_period: int = 120
    flow_period: int = 120
    internal_pivot_wing: int = 2
    external_pivot_wing: int = 2
    external_tf_minutes: int = 15
    range_tf_minutes: int = 240
    pool_expiry_minutes: int = 4_320
    max_pools_per_side: int = 72
    pool_merge_atr: float = 0.10
    min_pool_age_bars: int = 3
    probe_min_atr: float = 0.025
    probe_max_atr: float = 1.80
    probe_expiry_bars: int = 5
    min_probe_relative_volume: float = 0.65
    rejection_reclaim_atr: float = 0.015
    rejection_close_location: float = 0.48
    absorption_flow_min: float = 0.025
    opposite_flow_min: float = 0.035
    acceptance_close_atr: float = 0.025
    acceptance_body_atr: float = 0.12
    acceptance_close_location: float = 0.58
    acceptance_flow_min: float = 0.025
    acceptance_min_closes: int = 2
    confirmation_expiry_bars: int = 14
    mss_body_atr: float = 0.12
    mss_flow_min: float = 0.025
    mss_close_location: float = 0.58
    retest_tolerance_atr: float = 0.18
    retest_hold_atr: float = 0.00
    reacceleration_body_atr: float = 0.08
    reacceleration_flow_min: float = 0.015
    stop_buffer_atr: float = 0.08
    min_stop_atr: float = 0.10
    max_stop_atr: float = 2.20
    min_net_r: float = 1.05
    max_target_atr: float = 16.0
    risk_fraction: float = 0.03
    effective_maker_rate: float = 0.0004
    effective_taker_rate: float = 0.0008
    tick_slippage_units: float = 2.0
    price_increment: float = 0.1

    def __post_init__(self) -> None:
        integer_fields = (
            "atr_period", "volume_period", "flow_period", "internal_pivot_wing",
            "external_pivot_wing", "external_tf_minutes", "range_tf_minutes",
            "pool_expiry_minutes", "max_pools_per_side", "min_pool_age_bars",
            "probe_expiry_bars", "acceptance_min_closes", "confirmation_expiry_bars",
        )
        for name in integer_fields:
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be within (0, 0.03]")
        if self.min_stop_atr <= 0 or self.max_stop_atr <= self.min_stop_atr:
            raise ValueError("invalid stop-distance bounds")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be positive")

@dataclass(slots=True)
class LiquidityPool:
    pool_id: str
    side: Side
    price: float
    source: str
    event_time_ns: int
    observed_time_ns: int
    expires_time_ns: int
    formed_bar_index: int
    active: bool = True
    touches: int = 0

@dataclass(slots=True)
class ProbeState:
    scenario_id: str
    pool_id: str
    side: Side
    started_index: int
    started_ts_ns: int
    extreme: float
    sweep_flow: float
    relative_volume: float
    closes_outside: int = 0

@dataclass(slots=True)
class ConfirmationState:
    scenario_id: str
    pool_id: str
    kind: ScenarioKind
    direction: Direction
    started_index: int
    trigger_extreme: float
    structure_level: float | None
    retest_seen: bool = False

@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    scenario: ScenarioKind
    direction: Direction
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    loss_per_unit: float
    expected_profit_per_unit: float
    net_r: float
    details: dict[str, Any]

@dataclass(frozen=True, slots=True)
class SizeDecision:
    feasible: bool
    quantity: Decimal
    planned_loss_budget: Decimal
    expected_total_loss: Decimal
    required_margin: Decimal
    reason: str

class RiskSizer:
    """Size exactly from current NAV and costed loss per unit.

    No score multiplier, volatility multiplier, nominal cap, or arbitrary
    leverage cap is introduced here.  Venue quantity/min-notional/margin rules
    remain hard feasibility constraints.
    """

    def __init__(self, risk_fraction: float) -> None:
        fraction = Decimal(str(risk_fraction))
        if fraction <= 0 or fraction > Decimal("0.03"):
            raise ValueError("risk fraction must be within (0, 0.03]")
        self.risk_fraction = fraction

    @staticmethod
    def _floor_increment(value: Decimal, increment: Decimal) -> Decimal:
        if increment <= 0:
            raise ValueError("quantity increment must be positive")
        units = (value / increment).to_integral_value(rounding=ROUND_DOWN)
        return units * increment

    def size(
        self,
        *,
        nav: Decimal,
        loss_per_unit: Decimal,
        entry_price: Decimal,
        quantity_increment: Decimal,
        min_quantity: Decimal,
        min_notional: Decimal,
        margin_init: Decimal,
        free_balance: Decimal,
    ) -> SizeDecision:
        budget = nav * self.risk_fraction
        zero = Decimal("0")
        if nav <= zero or loss_per_unit <= zero or entry_price <= zero:
            return SizeDecision(False, zero, budget, zero, zero, "INVALID_RISK_INPUT")
        raw = budget / loss_per_unit
        quantity = self._floor_increment(raw, quantity_increment)
        if quantity < min_quantity:
            return SizeDecision(False, quantity, budget, quantity * loss_per_unit, zero, "BELOW_MIN_QUANTITY")
        notional = quantity * entry_price
        if notional < min_notional:
            return SizeDecision(False, quantity, budget, quantity * loss_per_unit, zero, "BELOW_MIN_NOTIONAL")
        expected = quantity * loss_per_unit
        # Rounding down must never exceed the selected 3% budget.
        if expected > budget:
            return SizeDecision(False, quantity, budget, expected, zero, "RISK_BUDGET_EXCEEDED")
        required_margin = notional * margin_init
        if required_margin > free_balance:
            return SizeDecision(False, quantity, budget, expected, required_margin, "INSUFFICIENT_MARGIN")
        return SizeDecision(True, quantity, budget, expected, required_margin, "OK")

@dataclass(slots=True)
class _AggBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
