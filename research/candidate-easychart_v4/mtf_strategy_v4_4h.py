"""Nautilus strategy wiring for the 4h->1h->15m/5m/1m hierarchy."""
from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from mtf_strategy_v4 import EasyChartMTFStrategy


class FourHourEasyChartConfig(StrategyConfig, frozen=True):
    instrument_ids: tuple[InstrumentId, ...]
    super_bar_types: tuple[BarType, ...]
    higher_bar_types: tuple[BarType, ...]
    decision_bar_types: tuple[BarType, ...]
    trigger_bar_types: tuple[BarType, ...]
    execution_bar_types: tuple[BarType, ...]
    risk_fraction: float = 0.03
    min_gross_rr: float = 1.0
    estimated_entry_fee_rate: float = 0.00075
    estimated_stop_fee_rate: float = 0.00075
    estimated_funding_rate: float = 0.00010
    estimated_entry_slippage_ticks: int = 2
    estimated_stop_slippage_ticks: int = 2
    trading_start_ns: int = 0


class FourHourEasyChartStrategy(EasyChartMTFStrategy):
    """One global account with a causally earlier 4h bar in each close bucket."""

    SUPER_MINUTES = 240

    def __init__(self, config: FourHourEasyChartConfig) -> None:
        if len(config.super_bar_types) != len(config.instrument_ids):
            raise ValueError("instrument IDs and 4h bar types must have equal length")
        super().__init__(config)
        for instrument_id, super_bar_type in zip(
            config.instrument_ids,
            config.super_bar_types,
            strict=True,
        ):
            self.route_by_key[super_bar_type.id_spec_key()] = (
                instrument_id,
                self.SUPER_MINUTES,
            )

    @classmethod
    def expected_composite_count(cls, ts_event: int, symbol_count: int) -> int:
        count = super().expected_composite_count(ts_event, symbol_count)
        minute = ts_event // cls.NS_PER_MINUTE
        if minute % cls.SUPER_MINUTES == 0:
            count += symbol_count
        return count

    def on_start(self) -> None:
        super().on_start()
        for super_bar_type in self.config.super_bar_types:
            self.subscribe_bars(super_bar_type)

    def on_stop(self) -> None:
        super().on_stop()
        for super_bar_type in self.config.super_bar_types:
            self.unsubscribe_bars(super_bar_type)


__all__ = ["FourHourEasyChartConfig", "FourHourEasyChartStrategy"]
