"""Pinned Nautilus smoke test for Bar -> completion QuoteTick -> market fill ordering."""

from __future__ import annotations

from decimal import Decimal
import unittest

import pandas as pd

from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import BarType

from quote_resiliency_native_quotes import completion_quote_ticks_from_frame
from run import _build_instrument
from run_aggtrade_acceptance_nautilus import (
    _bars_from_ten_second_frame,
    _create_engine,
    _native_instrument,
)


Strategy = nautilus_pyo3.Strategy
StrategyConfig = nautilus_pyo3.StrategyConfig
OrderSide = nautilus_pyo3.OrderSide
TimeInForce = nautilus_pyo3.TimeInForce
Quantity = nautilus_pyo3.Quantity


class _SmokeConfig(StrategyConfig):
    pass


class _QuoteMarketSmokeStrategy(Strategy):
    def __new__(cls, config, *_args, **_kwargs):
        return super().__new__(cls, config)

    def __init__(self, config, *, instrument_id, bar_type, expected_quote_time_ns):
        super().__init__(config)
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.expected_quote_time_ns = int(expected_quote_time_ns)
        self.bar_seen_time_ns = None
        self.quote_seen_time_ns = None
        self.fill_prices: list[float] = []
        self.fill_times_ns: list[int] = []

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)
        self.subscribe_quote_ticks(self.instrument_id)

    def on_bar(self, bar) -> None:
        self.bar_seen_time_ns = int(bar.ts_event)

    def on_quote_tick(self, tick) -> None:
        quote_time_ns = int(tick.ts_event)
        if quote_time_ns != self.expected_quote_time_ns:
            return
        if self.bar_seen_time_ns is None or quote_time_ns <= self.bar_seen_time_ns:
            raise RuntimeError("completion quote was not delivered strictly after the bar")
        self.quote_seen_time_ns = quote_time_ns
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_str("1.000"),
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            quote_quantity=False,
            tags=["NATIVE_QUOTE_SMOKE"],
        )
        self.submit_order(order)

    def on_order_filled(self, event) -> None:
        self.fill_prices.append(float(event.last_px.as_double()))
        self.fill_times_ns.append(int(event.ts_event))


class NativeQuoteExecutionSmokeContracts(unittest.TestCase):
    @staticmethod
    def _instrument():
        return _build_instrument(
            "BTCUSDT",
            {
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "base_currency": "BTC",
                "price_precision": 1,
                "size_precision": 3,
                "tick_size": "0.1",
                "size_increment": "0.001",
                "min_quantity": "0.001",
            },
            0.0006,
        )

    @staticmethod
    def _engine_config() -> dict:
        return {
            "starting_nav_usdt": 100000.0,
            "random_seed": 8811,
            "cost_assumptions": {
                "one_tick_slippage_probability": 1.0,
                "latency_ms": {"base": 0, "insert": 0, "update": 0, "cancel": 0},
            },
            "venue": {
                "default_leverage": 125,
                "bar_adaptive_high_low_ordering": True,
                "liquidation_enabled": True,
                "liquidation_trigger_ratio": 1.0,
                "liquidation_cancel_open_orders": True,
            },
        }

    def test_market_order_uses_completion_ask_plus_exactly_one_adverse_tick(self) -> None:
        instrument = self._instrument()
        native = _native_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-10-SECOND-LAST-EXTERNAL")
        bucket_end = pd.Timestamp("2023-10-15T00:00:10Z")
        bar_frame = pd.DataFrame(
            {
                "open": [99.8],
                "high": [100.4],
                "low": [99.5],
                "close": [100.0],
                "volume": [100.0],
            },
            index=pd.DatetimeIndex([bucket_end]),
        )
        bars = _bars_from_ten_second_frame(
            bar_frame,
            bar_type=bar_type,
            instrument=instrument,
        )
        source_event = pd.Timestamp("2023-10-15T00:00:09.999Z")
        quote_frame = pd.DataFrame(
            {
                "bid_close": [99.9],
                "ask_close": [100.1],
                "bid_qty_close": [100.0],
                "ask_qty_close": [100.0],
                "quote_last_event_ns": [int(source_event.as_unit("ns").value)],
                "native_quote_snapshot_observable": [True],
            },
            index=pd.DatetimeIndex([bucket_end]),
        )
        quotes, _quality = completion_quote_ticks_from_frame(
            quote_frame,
            instrument=instrument,
        )
        expected_quote_time_ns = int(quotes[0].ts_event)

        engine = _create_engine(self._engine_config(), {"BTCUSDT": native})
        strategy = _QuoteMarketSmokeStrategy(
            _SmokeConfig(),
            instrument_id=native.id,
            bar_type=bar_type.to_pyo3(),
            expected_quote_time_ns=expected_quote_time_ns,
        )
        engine.add_data([bars[0].to_pyo3()], None, False, True)
        engine.add_data([quotes[0].to_pyo3()], None, False, True)
        engine.sort_data()
        engine.add_strategy(strategy)
        try:
            engine.run()
            self.assertEqual(strategy.bar_seen_time_ns, int(bars[0].ts_event))
            self.assertEqual(strategy.quote_seen_time_ns, expected_quote_time_ns)
            self.assertEqual(strategy.fill_prices, [100.2])
            self.assertEqual(strategy.fill_times_ns, [expected_quote_time_ns])
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
