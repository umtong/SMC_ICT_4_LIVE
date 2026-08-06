"""One-shot API probe for the pinned NautilusTrader research image.

This is diagnostic only. It does not implement an execution or accounting engine.
Every reflection failure is isolated so one Cython/PyO3 type cannot hide the
remaining API facts.
"""

from __future__ import annotations

from importlib.metadata import version
import inspect

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


def show_signature(label: str, obj: object) -> None:
    try:
        value = inspect.signature(obj)
    except Exception as exc:  # reflection differs across Cython/PyO3 classes
        value = f"<unavailable: {type(exc).__name__}: {exc}>"
    print(label, value)
    doc = inspect.getdoc(obj)
    if doc:
        print(f"{label}.__doc__", doc[:1200].replace("\n", " | "))


def main() -> None:
    print("nautilus_trader", version("nautilus_trader"))
    for label, obj in (
        ("BacktestEngine.add_venue", BacktestEngine.add_venue),
        ("FillModel", FillModel),
        ("FillModel.__init__", FillModel.__init__),
        ("BarDataWrangler.process", BarDataWrangler.process),
        ("Strategy.submit_order", Strategy.submit_order),
        ("Strategy.submit_order_list", Strategy.submit_order_list),
        ("Strategy.close_all_positions", Strategy.close_all_positions),
        ("OrderFactory.market", OrderFactory.market),
        ("OrderFactory.bracket", OrderFactory.bracket),
        ("Portfolio.equity", Portfolio.equity),
        ("Money.as_double", Money.as_double),
        ("Price.as_double", Price.as_double),
        ("Quantity.as_double", Quantity.as_double),
        ("Bar", Bar),
    ):
        show_signature(label, obj)

    fill_model = FillModel(
        prob_fill_on_limit=1.0,
        prob_slippage=1.0,
        random_seed=20260806,
    )
    print("fill_model", fill_model)

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
        "min_notional",
    ):
        print(f"instrument.{name}", getattr(instrument, name, None))

    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
    print("bar_type", bar_type)
    print("Strategy.order_factory descriptor", getattr(Strategy, "order_factory", None))


if __name__ == "__main__":
    main()
