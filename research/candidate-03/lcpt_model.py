"Domain records for candidate-03 LCPT-v1."

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any


NS_PER_MINUTE = 60_000_000_000
NS_PER_DAY = 86_400_000_000_000


class ScenarioState(StrEnum):
    IDLE = "IDLE"
    IGNITION_CANDIDATE = "IGNITION_CANDIDATE"
    CASCADE_CONFIRMED = "CASCADE_CONFIRMED"
    ENTRY_BUFFER = "ENTRY_BUFFER"
    POSITION_ACTIVE = "POSITION_ACTIVE"
    INVALIDATED = "INVALIDATED"
    BLOCKED = "BLOCKED"
    CLOSED = "CLOSED"


class ExitReason(StrEnum):
    TARGET = "TARGET"
    STOP = "STOP"
    TRAIL = "TRAIL"
    TIME = "TIME"
    END_OF_RUN = "END_OF_RUN"


@dataclass(frozen=True, slots=True)
class LcptConfig:
    candidate: str = "candidate-03-lcpt-v1"
    futures_instrument_id: str = "BTCUSDT-PERP.BINANCE"
    spot_instrument_id: str = "BTCUSDT.BINANCE"
    initial_nav: float = 100_000.0
    risk_fraction: float = 0.03
    taker_fee_bps: float = 5.0
    slippage_impact_bps: float = 1.5
    funding_bps_per_8h: float = 1.0

    ignition_price_shock_bps: float = 10.0
    ignition_oi_drop_bps: float = 1.0
    continuation_oi_drop_bps: float = 20.0
    ignition_futures_flow_min: float = 0.0
    ignition_spot_flow_min: float = -0.10
    continuation_futures_flow_min: float = 0.0
    continuation_spot_flow_min: float = 0.0
    extension_through_ignition_max_bps: float = 50.0

    atr_minutes: int = 60
    stop_buffer_atr: float = 0.20
    entry_buffer_minutes: int = 1
    target_net_r: float = 6.0
    protection_activation_r: float = 2.0
    protection_lock_net_r: float = 0.5
    structural_trail_minutes: int = 20
    structural_trail_buffer_atr: float = 0.05
    max_holding_minutes: int = 240

    development_weeks: tuple[str, ...] = (
        "2022-03-07",
        "2025-03-17",
        "2022-07-18",
    )
    validation_weeks: tuple[str, ...] = (
        "2023-04-10",
        "2025-02-03",
        "2025-10-06",
    )

    minimum_trades: int = 8
    minimum_win_rate: float = 0.45
    minimum_daily_geometric_growth: float = 0.01
    maximum_mark_to_market_drawdown: float = 0.20

    def validate(self) -> None:
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if self.initial_nav <= 0:
            raise ValueError("initial_nav must be positive")
        for value in (
            self.taker_fee_bps,
            self.slippage_impact_bps,
            self.funding_bps_per_8h,
            self.ignition_price_shock_bps,
            self.ignition_oi_drop_bps,
            self.continuation_oi_drop_bps,
            self.extension_through_ignition_max_bps,
            self.stop_buffer_atr,
            self.target_net_r,
            self.protection_activation_r,
            self.protection_lock_net_r,
            self.structural_trail_buffer_atr,
        ):
            if value < 0:
                raise ValueError("cost and threshold values cannot be negative")
        for value in (
            self.atr_minutes,
            self.entry_buffer_minutes,
            self.structural_trail_minutes,
            self.max_holding_minutes,
            self.minimum_trades,
        ):
            if value <= 0:
                raise ValueError("time windows and minimum trades must be positive")
        if not 0 <= self.minimum_win_rate <= 1:
            raise ValueError("minimum_win_rate must be in [0, 1]")
        if not 0 < self.maximum_mark_to_market_drawdown < 1:
            raise ValueError("maximum drawdown gate must be in (0, 1)")
        if set(self.development_weeks) & set(self.validation_weeks):
            raise ValueError("development and validation weeks must not overlap")


@dataclass(frozen=True, slots=True)
class AggTrade:
    aggregate_id: int
    price: float
    quantity: float
    event_time_ns: int
    aggressor_sign: int

    def __post_init__(self) -> None:
        if self.aggregate_id < 0 or self.event_time_ns < 0:
            raise ValueError("trade identifiers and timestamps must be non-negative")
        if not isfinite(self.price) or not isfinite(self.quantity):
            raise ValueError("trade price and quantity must be finite")
        if self.price <= 0 or self.quantity <= 0:
            raise ValueError("trade price and quantity must be positive")
        if self.aggressor_sign not in (-1, 1):
            raise ValueError("aggressor_sign must be -1 or +1")

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass(slots=True)
class MinuteBar:
    minute_start_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    notional: float
    signed_notional: float
    trade_count: int
    first_trade_id: int
    last_trade_id: int
    first_event_time_ns: int
    last_event_time_ns: int

    @classmethod
    def from_trade(cls, trade: AggTrade) -> "MinuteBar":
        notional = trade.notional
        return cls(
            minute_start_ns=(trade.event_time_ns // NS_PER_MINUTE) * NS_PER_MINUTE,
            open=trade.price,
            high=trade.price,
            low=trade.price,
            close=trade.price,
            volume=trade.quantity,
            notional=notional,
            signed_notional=trade.aggressor_sign * notional,
            trade_count=1,
            first_trade_id=trade.aggregate_id,
            last_trade_id=trade.aggregate_id,
            first_event_time_ns=trade.event_time_ns,
            last_event_time_ns=trade.event_time_ns,
        )

    def add(self, trade: AggTrade) -> None:
        expected = (trade.event_time_ns // NS_PER_MINUTE) * NS_PER_MINUTE
        if expected != self.minute_start_ns:
            raise ValueError("trade does not belong to this minute")
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        self.close = trade.price
        self.volume += trade.quantity
        self.notional += trade.notional
        self.signed_notional += trade.aggressor_sign * trade.notional
        self.trade_count += 1
        self.last_trade_id = trade.aggregate_id
        self.last_event_time_ns = trade.event_time_ns

    @property
    def flow(self) -> float:
        return self.signed_notional / self.notional if self.notional > 0 else 0.0


@dataclass(frozen=True, slots=True)
class FiveMinuteBar:
    boundary_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    notional: float
    signed_notional: float
    trade_count: int

    @property
    def flow(self) -> float:
        return self.signed_notional / self.notional if self.notional > 0 else 0.0


@dataclass(frozen=True, slots=True)
class FiveMinuteState:
    boundary_ns: int
    futures: FiveMinuteBar
    spot: FiveMinuteBar
    open_interest: float
    futures_return_bps: float
    open_interest_change_bps: float


@dataclass(frozen=True, slots=True)
class CascadeSignal:
    scenario_id: str
    direction: int
    ignition_time_ns: int
    confirmation_time_ns: int
    cascade_high: float
    cascade_low: float
    atr: float
    stop_trigger_price: float
    ignition_return_bps: float
    ignition_oi_drop_bps: float
    continuation_return_bps: float
    continuation_oi_drop_bps: float
    ignition_futures_flow: float
    ignition_spot_flow: float
    continuation_futures_flow: float
    continuation_spot_flow: float
    extension_through_ignition_bps: float

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("signal direction must be -1 or +1")
        if self.confirmation_time_ns <= self.ignition_time_ns:
            raise ValueError("confirmation must follow ignition")
        if self.cascade_low >= self.cascade_high:
            raise ValueError("cascade range must be positive")
        if self.atr <= 0:
            raise ValueError("signal ATR must be positive")

    def details(self) -> dict[str, float | int | str]:
        return {
            "direction": "LONG" if self.direction > 0 else "SHORT",
            "ignition_return_bps": self.ignition_return_bps,
            "ignition_oi_drop_bps": self.ignition_oi_drop_bps,
            "continuation_return_bps": self.continuation_return_bps,
            "continuation_oi_drop_bps": self.continuation_oi_drop_bps,
            "ignition_futures_flow": self.ignition_futures_flow,
            "ignition_spot_flow": self.ignition_spot_flow,
            "continuation_futures_flow": self.continuation_futures_flow,
            "continuation_spot_flow": self.continuation_spot_flow,
            "extension_through_ignition_bps": self.extension_through_ignition_bps,
            "cascade_high": self.cascade_high,
            "cascade_low": self.cascade_low,
            "signal_atr": self.atr,
            "stop_trigger_price": self.stop_trigger_price,
        }


@dataclass(slots=True)
class Position:
    signal: CascadeSignal
    entry_trade_id: int
    entry_time_ns: int
    entry_raw_price: float
    entry_fill_price: float
    quantity: float
    planned_loss: float
    expected_loss_per_unit: float
    expected_stop_fill_price: float
    target_trigger_price: float
    current_stop_price: float
    expiry_time_ns: int
    max_funding_per_unit: float
    mfe_r: float = 0.0
    mae_r: float = 0.0
    protection_active: bool = False


@dataclass(frozen=True, slots=True)
class TradeRecord:
    scenario_id: str
    direction: str
    ignition_time_ns: int
    confirmation_time_ns: int
    entry_time_ns: int
    exit_time_ns: int
    entry_trade_id: int
    exit_trade_id: int
    entry_raw_price: float
    entry_fill_price: float
    exit_raw_price: float
    exit_fill_price: float
    initial_stop_price: float
    final_stop_price: float
    target_trigger_price: float
    quantity: float
    nav_before: float
    nav_after: float
    planned_loss: float
    expected_loss_per_unit: float
    net_pnl: float
    net_r: float
    holding_minutes: float
    funding_cost: float
    mfe_r: float
    mae_r: float
    exit_reason: str
    feature_details: dict[str, Any] = field(default_factory=dict)
