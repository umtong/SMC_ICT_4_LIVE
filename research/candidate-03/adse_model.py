"""Domain contracts for candidate-03 ADSE-v1."""
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
    PULLBACK_OBSERVED = "PULLBACK_OBSERVED"
    REACCELERATION_CONFIRMED = "REACCELERATION_CONFIRMED"
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
class ExitProfile:
    target_net_r: float
    protection_activation_r: float
    protection_lock_net_r: float
    structural_trail_minutes: int
    structural_trail_buffer_atr: float
    max_holding_minutes: int

    def validate(self) -> None:
        if self.target_net_r <= 0 or self.protection_activation_r <= 0:
            raise ValueError("target and activation R must be positive")
        if self.protection_lock_net_r < 0 or self.structural_trail_buffer_atr < 0:
            raise ValueError("lock and trail buffer cannot be negative")
        if self.structural_trail_minutes <= 0 or self.max_holding_minutes <= 0:
            raise ValueError("trail and holding windows must be positive")


@dataclass(frozen=True, slots=True)
class AdseConfig:
    candidate: str = "candidate-03-adse-v1"
    futures_instrument_id: str = "BTCUSDT-PERP.BINANCE"
    spot_instrument_id: str = "BTCUSDT.BINANCE"
    initial_nav: float = 100_000.0
    risk_fraction: float = 0.03
    taker_fee_bps: float = 5.0
    slippage_impact_bps: float = 1.5
    funding_bps_per_8h: float = 1.0

    regime_oi_lookback_states: int = 72
    regime_oi_min_states: int = 36
    regime_atr_lookback_minutes: int = 360
    regime_atr_min_minutes: int = 180
    lcpt_regime_ratio_max: float = 1.40
    tpr_regime_ratio_min: float = 1.00

    ignition_price_shock_bps: float = 10.0
    ignition_oi_drop_bps: float = 1.0
    continuation_oi_drop_bps: float = 20.0
    ignition_futures_flow_min: float = 0.0
    ignition_spot_flow_min: float = -0.10
    continuation_futures_flow_min: float = 0.0
    continuation_spot_flow_min: float = 0.0
    extension_through_ignition_max_bps: float = 50.0

    tpr_trend_minutes: int = 60
    tpr_trend_min_bps: float = 20.0
    tpr_trend_max_bps: float = 200.0
    tpr_pullback_min_bps: float = 5.0
    tpr_resumption_min_bps: float = 5.0
    tpr_pullback_futures_flow_max: float = 0.0
    tpr_resumption_futures_flow_min: float = 0.0
    tpr_resumption_spot_flow_min: float = 0.0

    atr_minutes: int = 60
    entry_buffer_minutes: int = 1
    lcpt_stop_buffer_atr: float = 0.20
    tpr_stop_buffer_atr: float = 0.10

    lcpt_target_net_r: float = 6.0
    lcpt_protection_activation_r: float = 2.0
    lcpt_protection_lock_net_r: float = 0.5
    lcpt_structural_trail_minutes: int = 20
    lcpt_structural_trail_buffer_atr: float = 0.05
    lcpt_max_holding_minutes: int = 240

    tpr_target_net_r: float = 3.0
    tpr_protection_activation_r: float = 1.5
    tpr_protection_lock_net_r: float = 0.5
    tpr_structural_trail_minutes: int = 15
    tpr_structural_trail_buffer_atr: float = 0.05
    tpr_max_holding_minutes: int = 180

    development_weeks: tuple[str, ...] = (
        "2022-03-07", "2025-03-17", "2022-07-18", "2023-04-10",
    )
    validation_weeks: tuple[str, ...] = (
        "2025-05-05", "2022-09-19", "2023-06-05",
    )

    minimum_trades: int = 8
    minimum_win_rate: float = 0.45
    minimum_daily_geometric_growth: float = 0.01
    maximum_mark_to_market_drawdown: float = 0.20

    @property
    def lcpt_exit(self) -> ExitProfile:
        return ExitProfile(
            self.lcpt_target_net_r,
            self.lcpt_protection_activation_r,
            self.lcpt_protection_lock_net_r,
            self.lcpt_structural_trail_minutes,
            self.lcpt_structural_trail_buffer_atr,
            self.lcpt_max_holding_minutes,
        )

    @property
    def tpr_exit(self) -> ExitProfile:
        return ExitProfile(
            self.tpr_target_net_r,
            self.tpr_protection_activation_r,
            self.tpr_protection_lock_net_r,
            self.tpr_structural_trail_minutes,
            self.tpr_structural_trail_buffer_atr,
            self.tpr_max_holding_minutes,
        )

    def validate(self) -> None:
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if self.initial_nav <= 0:
            raise ValueError("initial_nav must be positive")
        if min(self.taker_fee_bps, self.slippage_impact_bps, self.funding_bps_per_8h) < 0:
            raise ValueError("cost assumptions cannot be negative")
        if not 0 < self.tpr_regime_ratio_min < self.lcpt_regime_ratio_max:
            raise ValueError("regime thresholds must form a positive overlap band")
        if self.tpr_trend_minutes % 5:
            raise ValueError("TPR trend window must be divisible by five")
        for value in (
            self.regime_oi_lookback_states, self.regime_oi_min_states,
            self.regime_atr_lookback_minutes, self.regime_atr_min_minutes,
            self.tpr_trend_minutes, self.atr_minutes, self.entry_buffer_minutes,
            self.minimum_trades,
        ):
            if value <= 0:
                raise ValueError("windows and minimum trades must be positive")
        if self.regime_oi_min_states > self.regime_oi_lookback_states:
            raise ValueError("OI regime minimum exceeds lookback")
        if self.regime_atr_min_minutes > self.regime_atr_lookback_minutes:
            raise ValueError("ATR regime minimum exceeds lookback")
        if set(self.development_weeks) & set(self.validation_weeks):
            raise ValueError("development and validation weeks overlap")
        if not 0 <= self.minimum_win_rate <= 1:
            raise ValueError("minimum win rate must be in [0, 1]")
        if not 0 < self.maximum_mark_to_market_drawdown < 1:
            raise ValueError("drawdown gate must be in (0, 1)")
        self.lcpt_exit.validate()
        self.tpr_exit.validate()


@dataclass(frozen=True, slots=True)
class AggTrade:
    aggregate_id: int
    price: float
    quantity: float
    event_time_ns: int
    aggressor_sign: int

    def __post_init__(self) -> None:
        if self.aggregate_id < 0 or self.event_time_ns < 0:
            raise ValueError("identifiers and timestamps must be non-negative")
        if not isfinite(self.price) or not isfinite(self.quantity):
            raise ValueError("price and quantity must be finite")
        if self.price <= 0 or self.quantity <= 0:
            raise ValueError("price and quantity must be positive")
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
        n = trade.notional
        return cls(
            (trade.event_time_ns // NS_PER_MINUTE) * NS_PER_MINUTE,
            trade.price, trade.price, trade.price, trade.price,
            trade.quantity, n, trade.aggressor_sign * n, 1,
            trade.aggregate_id, trade.aggregate_id,
            trade.event_time_ns, trade.event_time_ns,
        )

    def add(self, trade: AggTrade) -> None:
        if (trade.event_time_ns // NS_PER_MINUTE) * NS_PER_MINUTE != self.minute_start_ns:
            raise ValueError("trade does not belong to minute")
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
    futures_return_bps: float | None
    open_interest_change_bps: float | None


@dataclass(frozen=True, slots=True)
class ScenarioSignal:
    scenario_id: str
    scenario_kind: str
    direction: int
    hypothesis_time_ns: int
    confirmation_time_ns: int
    stop_trigger_price: float
    atr: float
    regime_ratio: float
    buffer_direction_required: bool
    exit_profile: ExitProfile
    features: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.scenario_kind not in ("LCPT", "TPR"):
            raise ValueError("unsupported scenario kind")
        if self.direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if self.confirmation_time_ns <= self.hypothesis_time_ns:
            raise ValueError("confirmation must follow hypothesis")
        if self.stop_trigger_price <= 0 or self.atr <= 0:
            raise ValueError("stop and ATR must be positive")

    def details(self) -> dict[str, Any]:
        return {
            "scenario_kind": self.scenario_kind,
            "direction": "LONG" if self.direction > 0 else "SHORT",
            "regime_ratio": self.regime_ratio,
            "stop_trigger_price": self.stop_trigger_price,
            "signal_atr": self.atr,
            "buffer_direction_required": self.buffer_direction_required,
            "exit_profile": {
                "target_net_r": self.exit_profile.target_net_r,
                "protection_activation_r": self.exit_profile.protection_activation_r,
                "protection_lock_net_r": self.exit_profile.protection_lock_net_r,
                "structural_trail_minutes": self.exit_profile.structural_trail_minutes,
                "structural_trail_buffer_atr": self.exit_profile.structural_trail_buffer_atr,
                "max_holding_minutes": self.exit_profile.max_holding_minutes,
            },
            **self.features,
        }


@dataclass(slots=True)
class Position:
    signal: ScenarioSignal
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
    maximum_funding_per_unit: float
    mfe_r: float = 0.0
    mae_r: float = 0.0
    protection_active: bool = False


@dataclass(frozen=True, slots=True)
class TradeRecord:
    scenario_id: str
    scenario_kind: str
    direction: str
    hypothesis_time_ns: int
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
