"""Minimal NautilusTrader shell around the EasyChart v2 state engine."""
from __future__ import annotations

from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.trading.strategy import Strategy

from model import EasyChartStateEngine, TradePlan
from strategy_orders import EasyChartOrderMixin
from strategy_runtime import EasyChartRuntimeMixin


class EasyChartV2Config(StrategyConfig, frozen=True):
    instrument_ids: tuple[InstrumentId, ...]
    signal_bar_types: tuple[BarType, ...]
    execution_bar_types: tuple[BarType, ...]
    risk_fraction: float = 0.03
    min_gross_rr: float = 1.0
    pivot_spans: tuple[int, ...] = (2, 6, 12)
    min_prominence_atr: float = 1.0
    estimated_entry_fee_rate: float = 0.00075
    estimated_stop_fee_rate: float = 0.00075
    estimated_funding_rate: float = 0.00010
    # The bar execution diagnostic produced aggregate entry and stop fills up to
    # about two price increments away from the signal/trigger. These reserves are
    # execution-cost estimates inside the 3% loss budget, not risk multipliers.
    estimated_entry_slippage_ticks: int = 2
    estimated_stop_slippage_ticks: int = 2
    enable_rejection: bool = True
    enable_acceptance: bool = True
    trading_start_ns: int = 0


class EasyChartV2Strategy(EasyChartRuntimeMixin, EasyChartOrderMixin, Strategy):
    def __init__(self, config: EasyChartV2Config) -> None:
        super().__init__(config)
        count = len(config.instrument_ids)
        if len(config.signal_bar_types) != count or len(config.execution_bar_types) != count:
            raise ValueError("instrument_ids and both bar type tuples must have equal length")
        if config.estimated_entry_slippage_ticks < 0 or config.estimated_stop_slippage_ticks < 0:
            raise ValueError("estimated slippage ticks cannot be negative")

        self.instruments: dict[InstrumentId, Any] = {}
        self.engines: dict[InstrumentId, EasyChartStateEngine] = {}
        self.signal_to_instrument = {
            bar_type.id_spec_key(): instrument_id
            for bar_type, instrument_id in zip(config.signal_bar_types, config.instrument_ids, strict=True)
        }
        self.execution_to_instrument = dict(zip(config.execution_bar_types, config.instrument_ids, strict=True))

        # One global pending entry or position across all four instruments.
        self.active_plan: TradePlan | None = None
        self.active_instrument_id: InstrumentId | None = None
        self.active_entry_id: ClientOrderId | None = None
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False

        # Buffer equal-timestamp signals before deterministic arbitration.
        self.signal_bucket_ts: int | None = None
        self.signal_bucket_seen: set[InstrumentId] = set()
        self.signal_bucket_plans: list[tuple[InstrumentId, TradePlan]] = []
        self.event_log: list[dict[str, Any]] = []
        self.plan_log: dict[str, TradePlan] = {}
