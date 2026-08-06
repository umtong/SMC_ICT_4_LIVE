#!/usr/bin/env python3
"""Executable contract probe for NautilusTrader 1.230.0 trade ticks."""
from __future__ import annotations

import inspect
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType, AggressorSide, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy


def main() -> int:
    venue = Venue("BINANCE")
    instrument_id = InstrumentId(Symbol("BTCUSDT-PERP"), venue)
    print("TradeTick module", TradeTick.__module__)
    print("TradeTick doc", TradeTick.__doc__)
    try:
        print("TradeTick signature", inspect.signature(TradeTick))
    except Exception as exc:
        print("TradeTick signature error", type(exc).__name__, str(exc))
    print(
        "AggressorSide members",
        [name for name in dir(AggressorSide) if name.isupper()],
    )
    print("Strategy subscribe_trade_ticks", hasattr(Strategy, "subscribe_trade_ticks"))
    print("Strategy on_trade_tick", hasattr(Strategy, "on_trade_tick"))

    tick = TradeTick(
        instrument_id=instrument_id,
        price=Price.from_str("30000.0"),
        size=Quantity.from_str("0.001"),
        aggressor_side=AggressorSide.BUYER,
        trade_id=TradeId("1"),
        ts_event=1_000_000_000,
        ts_init=1_000_000_000,
    )
    print("tick", tick)
    print("tick price", tick.price, "size", tick.size, "side", tick.aggressor_side)

    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    instrument = CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=btc,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        min_quantity=Quantity.from_str("0.001"),
        min_notional=Money(10.0, usdt),
        max_price=Price.from_str("10000000.0"),
        min_price=Price.from_str("0.1"),
        margin_init=Decimal("0.008"),
        margin_maint=Decimal("0.004"),
        maker_fee=Decimal("0.0007"),
        taker_fee=Decimal("0.0007"),
    )
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(100000.0, usdt)],
            base_currency=usdt,
            default_leverage=Decimal("125"),
        )
        engine.add_instrument(instrument)
        ticks = [
            TradeTick(
                instrument_id=instrument_id,
                price=Price.from_str(price),
                size=Quantity.from_str("0.001"),
                aggressor_side=(
                    AggressorSide.BUYER if index % 2 == 0 else AggressorSide.SELLER
                ),
                trade_id=TradeId(str(index + 1)),
                ts_event=(index + 1) * 1_000_000_000,
                ts_init=(index + 1) * 1_000_000_000,
            )
            for index, price in enumerate(("30000.0", "30001.0", "30002.0"))
        ]
        engine.add_data(ticks)
        engine.run()
        print("engine tick smoke OK", len(ticks))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
