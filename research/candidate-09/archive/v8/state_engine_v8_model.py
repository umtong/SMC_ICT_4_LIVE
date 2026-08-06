"""Causal session-auction state and risk contracts for candidate-09 v8."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from math import isfinite
from typing import Any, Mapping

MINUTE_NS = 60_000_000_000
DAY_NS = 1_440 * MINUTE_NS


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
class SessionSpec:
    name: str
    start_minute_utc: int
    end_minute_utc: int
    tradable: bool


@dataclass(frozen=True, slots=True)
class EngineConfig:
    sessions: tuple[SessionSpec, ...]
    atr_period: int = 20
    volume_period: int = 60
    approach_period: int = 12
    mss_lookback_bars: int = 3
    minimum_breach_atr: float = 0.08
    acceptance_buffer_atr: float = 0.08
    acceptance_closes: int = 2
    acceptance_timeout_bars: int = 8
    minimum_acceptance_displacement_atr: float = 0.35
    minimum_volume_ratio: float = 1.0
    minimum_approach_efficiency: float = 0.05
    minimum_approach_flow: float = 0.02
    directional_imbalance: float = 0.08
    failure_buffer_atr: float = 0.06
    minimum_failure_displacement_atr: float = 0.25
    failure_timeout_bars: int = 18
    failure_retest_timeout_bars: int = 10
    retest_tolerance_atr: float = 0.20
    stop_buffer_atr: float = 0.12
    minimum_net_reward_to_risk: float = 1.20
    composite_cost_per_fill: float = 0.00075
    cooldown_bars: int = 6
    use_flow_confirmation: bool = True
    require_acceptance_confirmation: bool = True
    require_failure_retest: bool = False
    use_midpoint_target: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {"baseline", "no-acceptance", "failure-retest", "midpoint-target"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        structure = payload["structure"]
        setup = payload["setup"]
        trade = payload["trade"]
        risk = payload["risk"]
        sessions = tuple(
            SessionSpec(
                name=str(item["name"]),
                start_minute_utc=int(item["start_minute_utc"]),
                end_minute_utc=int(item["end_minute_utc"]),
                tradable=bool(item["tradable"]),
            )
            for item in structure["sessions_utc"]
        )
        if not sessions or sessions[0].start_minute_utc != 0 or sessions[-1].end_minute_utc != 1440:
            raise ValueError("session schedule must cover one UTC day")
        previous_end = 0
        names: set[str] = set()
        for session in sessions:
            if session.name in names:
                raise ValueError("session names must be unique")
            names.add(session.name)
            if session.start_minute_utc != previous_end or session.end_minute_utc <= session.start_minute_utc:
                raise ValueError("sessions must be contiguous and ascending")
            previous_end = session.end_minute_utc
        return cls(
            sessions=sessions,
            atr_period=int(structure["atr_period"]),
            volume_period=int(structure["volume_period"]),
            approach_period=int(structure["approach_period"]),
            mss_lookback_bars=int(structure["mss_lookback_bars"]),
            minimum_breach_atr=float(setup["minimum_breach_atr"]),
            acceptance_buffer_atr=float(setup["acceptance_buffer_atr"]),
            acceptance_closes=int(setup["acceptance_closes"]),
            acceptance_timeout_bars=int(setup["acceptance_timeout_bars"]),
            minimum_acceptance_displacement_atr=float(setup["minimum_acceptance_displacement_atr"]),
            minimum_volume_ratio=float(setup["minimum_volume_ratio"]),
            minimum_approach_efficiency=float(setup["minimum_approach_efficiency"]),
            minimum_approach_flow=float(setup["minimum_approach_flow"]),
            directional_imbalance=float(setup["directional_imbalance"]),
            failure_buffer_atr=float(setup["failure_buffer_atr"]),
            minimum_failure_displacement_atr=float(setup["minimum_failure_displacement_atr"]),
            failure_timeout_bars=int(setup["failure_timeout_bars"]),
            failure_retest_timeout_bars=int(setup["failure_retest_timeout_bars"]),
            retest_tolerance_atr=float(setup["retest_tolerance_atr"]),
            stop_buffer_atr=float(setup["stop_buffer_atr"]),
            minimum_net_reward_to_risk=float(trade["minimum_net_reward_to_risk"]),
            composite_cost_per_fill=float(risk["composite_taker_cost_per_fill"]),
            cooldown_bars=int(trade["cooldown_bars"]),
            require_acceptance_confirmation=ablation != "no-acceptance",
            require_failure_retest=ablation == "failure-retest",
            use_midpoint_target=ablation == "midpoint-target",
        )


@dataclass(slots=True)
class SessionRange:
    range_id: str
    session_name: str
    start_ns: int
    end_ns: int
    high: float
    low: float
    close: float
    bars: int

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(slots=True)
class SessionBuilder:
    key: int
    session_index: int
    session_name: str
    start_ns: int
    end_ns: int
    high: float
    low: float
    close: float
    bars: int = 1


@dataclass(slots=True)
class LiquidityLevel:
    level_id: str
    kind: str
    price: float
    source: SessionRange
    consumed: bool = False


@dataclass(slots=True)
class PendingAcceptanceFailure:
    scenario_id: str
    level: LiquidityLevel
    breach_direction: str
    state: str
    breach_index: int
    extreme: float
    approach_efficiency: float
    approach_flow: float
    active_session_key: int
    active_session_name: str
    outside_closes: int = 0
    acceptance_index: int | None = None
    acceptance_displacement_seen: bool = False
    acceptance_flow_seen: bool = False
    max_volume_ratio: float = 0.0
    failure_index: int | None = None
    failure_high: float | None = None
    failure_low: float | None = None


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
