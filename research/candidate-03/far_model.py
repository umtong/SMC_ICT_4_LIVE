"""Domain records for candidate-03 FAR-v2.

FAR-v2 separates observable aggressive-flow absorption from the later market-
structure confirmation required to trade it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


class ScenarioState(StrEnum):
    IDLE = "IDLE"
    STRETCHED_CHASE = "STRETCHED_CHASE"
    ABSORPTION_OBSERVED = "ABSORPTION_OBSERVED"
    CHOCH_PENDING = "CHOCH_PENDING"
    ENTRY_PENDING = "ENTRY_PENDING"
    POSITION_ACTIVE = "POSITION_ACTIVE"
    CLOSED = "CLOSED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class ExitReason(StrEnum):
    TARGET = "TARGET"
    STOP = "STOP"
    TIME = "TIME"
    END_OF_RUN = "END_OF_RUN"


@dataclass(frozen=True, slots=True)
class FarConfig:
    candidate: str = "candidate-03-far-v2"
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    initial_nav: float = 100_000.0
    risk_fraction: float = 0.03
    taker_fee_bps: float = 5.0
    slippage_impact_bps: float = 1.5
    funding_bps_per_8h: float = 1.0

    # Observable absorption detector.
    flow_imbalance_min: float = 0.30
    activity_ratio_min: float = 2.0
    equilibrium_window_minutes: int = 240
    equilibrium_z_min: float = 0.8
    activity_baseline_minutes: int = 360
    activity_min_history_minutes: int = 120
    atr_window_minutes: int = 60
    rejection_location_min: float = 0.45
    directional_progress_max_bps: float = 1.0

    # State and structure confirmation.
    equilibrium_excursion_max_minutes: int = 120
    choch_lookback_minutes: int = 10
    choch_wait_minutes: int = 15
    one_attempt_per_excursion: bool = True
    episode_cooldown_minutes: int = 60

    # Causal invalidation and recovery objective.
    stop_buffer_atr: float = 0.20
    target_net_r: float = 3.0
    max_holding_minutes: int = 240
    warmup_minutes: int = 1_440

    development_weeks: tuple[str, ...] = ("2022-03-07", "2025-03-17")
    validation_salt: str = "candidate-03|far-v2|BTCUSDT"
    validation_weeks: tuple[str, ...] = ("2022-07-18", "2021-12-13", "2021-01-11")

    def validate(self) -> None:
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if self.initial_nav <= 0:
            raise ValueError("initial_nav must be positive")
        if min(self.taker_fee_bps, self.slippage_impact_bps, self.funding_bps_per_8h) < 0:
            raise ValueError("cost assumptions cannot be negative")
        if not 0 < self.flow_imbalance_min < 1:
            raise ValueError("flow_imbalance_min must be in (0, 1)")
        if self.activity_ratio_min <= 1:
            raise ValueError("activity_ratio_min must exceed one")
        if self.equilibrium_z_min <= 0 or self.rejection_location_min <= 0:
            raise ValueError("signal thresholds must be positive")
        if self.target_net_r <= 0 or self.stop_buffer_atr < 0:
            raise ValueError("exit geometry must be positive")
        for value in (
            self.equilibrium_window_minutes,
            self.activity_baseline_minutes,
            self.activity_min_history_minutes,
            self.atr_window_minutes,
            self.equilibrium_excursion_max_minutes,
            self.choch_lookback_minutes,
            self.choch_wait_minutes,
            self.max_holding_minutes,
            self.episode_cooldown_minutes,
            self.warmup_minutes,
        ):
            if value <= 0:
                raise ValueError("time windows must be positive")
        if self.activity_min_history_minutes > self.activity_baseline_minutes:
            raise ValueError("minimum activity history exceeds baseline window")
        if set(self.development_weeks) & set(self.validation_weeks):
            raise ValueError("development and validation weeks must be disjoint")
        if len(set(self.validation_weeks)) != len(self.validation_weeks):
            raise ValueError("validation weeks must be unique")

    @property
    def permitted_weeks(self) -> tuple[str, ...]:
        return self.development_weeks + self.validation_weeks


@dataclass(frozen=True, slots=True)
class AggTrade:
    aggregate_id: int
    price: float
    quantity: float
    event_time_ns: int
    buyer_maker: bool

    def __post_init__(self) -> None:
        if self.aggregate_id < 0 or self.event_time_ns < 0:
            raise ValueError("trade identifiers and time must be non-negative")
        if not isfinite(self.price) or not isfinite(self.quantity) or self.price <= 0 or self.quantity <= 0:
            raise ValueError("trade price and quantity must be finite and positive")

    @property
    def aggressor_sign(self) -> int:
        return -1 if self.buyer_maker else 1

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass(slots=True)
class MinuteBar:
    minute_index: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    notional: float = 0.0
    signed_notional: float = 0.0
    aggregate_trade_count: int = 0
    first_event_time_ns: int = 0
    last_event_time_ns: int = 0

    @classmethod
    def from_values(
        cls,
        minute_index: int,
        price: float,
        quantity: float,
        event_time_ns: int,
        aggressor_sign: int,
    ) -> "MinuteBar":
        notional = price * quantity
        return cls(
            minute_index=minute_index,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=quantity,
            notional=notional,
            signed_notional=aggressor_sign * notional,
            aggregate_trade_count=1,
            first_event_time_ns=event_time_ns,
            last_event_time_ns=event_time_ns,
        )

    def add_values(
        self,
        price: float,
        quantity: float,
        event_time_ns: int,
        aggressor_sign: int,
    ) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += quantity
        notional = price * quantity
        self.notional += notional
        self.signed_notional += aggressor_sign * notional
        self.aggregate_trade_count += 1
        self.last_event_time_ns = event_time_ns

    @property
    def observed_time_ns(self) -> int:
        return (self.minute_index + 1) * 60_000_000_000 - 1


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    observed_time_ns: int
    open: float
    high: float
    low: float
    close: float
    atr: float
    flow_imbalance: float
    activity_ratio: float
    equilibrium_price: float
    equilibrium_sigma: float
    equilibrium_z: float
    equilibrium_side: int
    equilibrium_excursion_minutes: int
    equilibrium_excursion_start_minute: int
    return_bps: float
    directional_progress_bps: float
    close_location: float
    rejection_location: float
    aggregate_trade_count: int
    notional: float


@dataclass(frozen=True, slots=True)
class AbsorptionSignal:
    scenario_id: str
    direction: Direction
    snapshot: FeatureSnapshot

    @property
    def excursion_id(self) -> tuple[int, int]:
        return (
            self.snapshot.equilibrium_side,
            self.snapshot.equilibrium_excursion_start_minute,
        )


@dataclass(frozen=True, slots=True)
class ChochSetup:
    signal: AbsorptionSignal
    confirmation_price: float
    invalidation_price: float
    expires_time_ns: int


@dataclass(slots=True)
class Position:
    scenario_id: str
    direction: Direction
    signal_time_ns: int
    entry_time_ns: int
    entry_trade_id: int
    entry_raw_price: float
    entry_fill_price: float
    stop_trigger_price: float
    target_trigger_price: float
    quantity: float
    nav_before: float
    planned_loss: float
    expected_loss_per_unit: float
    entry_fee: float
    signal_low: float
    signal_high: float
    signal_atr: float
    feature_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FarTrade:
    scenario_id: str
    direction: Direction
    signal_time_ns: int
    entry_time_ns: int
    exit_time_ns: int
    entry_trade_id: int
    exit_trade_id: int
    entry_raw_price: float
    entry_fill_price: float
    exit_raw_price: float
    exit_fill_price: float
    stop_trigger_price: float
    target_trigger_price: float
    quantity: float
    nav_before: float
    nav_after: float
    planned_loss: float
    net_pnl: float
    net_r: float
    holding_minutes: float
    funding_cost: float
    exit_reason: ExitReason
    feature_details: dict[str, Any]
