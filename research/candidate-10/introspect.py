"""One-shot API probe for the pinned NautilusTrader research image.

This is intentionally diagnostic only. It does not implement a backtest engine.
"""

from __future__ import annotations

from importlib.metadata import version
import inspect

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


def main() -> None:
    print("nautilus_trader", version("nautilus_trader"))
    print("BacktestEngine.add_venue", inspect.signature(BacktestEngine.add_venue))
    print("FillModel", inspect.signature(FillModel))
    print("BarDataWrangler.process", inspect.signature(BarDataWrangler.process))
    print("Strategy.submit_order_list", inspect.signature(Strategy.submit_order_list))
    print("Strategy.close_all_positions", inspect.signature(Strategy.close_all_positions))

    provider_methods = [name for name in dir(TestInstrumentProvider) if "binance" in name.lower()]
    print("provider_methods", provider_methods)

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    print("instrument", instrument)
    print("instrument.id", instrument.id)
    for name in (
        "price_precision",
        "size_precision",
        "price_increment",
        "size_increment",
        "maker_fee",
        "taker_fee",
        "margin_init",
        "margin_maint",
        "max_quantity",
        "min_quantity",
    ):
        print(f"instrument.{name}", getattr(instrument, name, None))

    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
    print("bar_type", bar_type)

    # OrderFactory is runtime-bound, but its class can be reached through a temporary strategy.
    print("Strategy.order_factory descriptor", getattr(Strategy, "order_factory", None))


if __name__ == "__main__":
    main()
