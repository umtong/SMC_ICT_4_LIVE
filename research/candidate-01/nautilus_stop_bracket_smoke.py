#!/usr/bin/env python3
"""Non-performance smoke for a NautilusTrader STOP_LIMIT bracket entry."""
from __future__ import annotations

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import (
    AccountType,
    AggressorSide,
    OmsType,
    OrderSide,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.events import PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy


class Config(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    signal_ns: int


class Smoke(Strategy):
    def __init__(self, config: Config, instrument: CryptoPerpetual) -> None:
        super().__init__(config)
        self.instrument = instrument
        self.submitted = False
        self.opened = 0
        self.closed = 0

    def on_start(self) -> None:
        self.subscribe_trade_ticks(self.config.instrument_id)

    def on_trade_tick(self, tick: TradeTick) -> None:
        if self.submitted or int(tick.ts_event) <= self.config.signal_ns:
            return
        self.submitted = True
        orders = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(0.010),
            entry_order_type=OrderType.STOP_LIMIT,
            entry_trigger_price=self.instrument.make_price(101.0),
            entry_price=self.instrument.make_price(101.1),
            time_in_force=TimeInForce.GTC,
            tp_price=self.instrument.make_price(103.0),
            sl_trigger_price=self.instrument.make_price(99.0),
        )
        self.submit_order_list(orders)

    def on_position_opened(self, event: PositionOpened) -> None:
        self.opened += 1
        print("POSITION_OPENED", event)

    def on_position_closed(self, event: PositionClosed) -> None:
        self.closed += 1
        print("POSITION_CLOSED", event)


def main() -> int:
    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("BTCUSDT-PERP"), venue),
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
    base_ns = 1_704_067_200_000_000_000
    # The parent is submitted at 100.5, triggered by the later 101.1 buyer
    # trade, filled by a subsequent 101.0 seller trade inside the 101.1 cap,
    # and finally closed at the 103.0 take-profit.
    prices = (
        100.0,
        100.2,
        100.5,
        100.9,
        101.1,
        101.0,
        101.2,
        102.0,
        103.1,
        103.0,
        102.9,
    )
    aggressors = (
        AggressorSide.BUYER,
        AggressorSide.BUYER,
        AggressorSide.BUYER,
        AggressorSide.BUYER,
        AggressorSide.BUYER,
        AggressorSide.SELLER,
        AggressorSide.BUYER,
        AggressorSide.BUYER,
        AggressorSide.BUYER,
        AggressorSide.SELLER,
        AggressorSide.SELLER,
    )
    ticks = [
        TradeTick(
            instrument_id=instrument.id,
            price=instrument.make_price(price),
            size=instrument.make_qty(1.0),
            aggressor_side=aggressors[index],
            trade_id=TradeId(str(index + 1)),
            ts_event=base_ns + index * 1_000_000_000,
            ts_init=base_ns + index * 1_000_000_000,
        )
        for index, price in enumerate(prices)
    ]
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    strategy = Smoke(
        Config(instrument_id=instrument.id, signal_ns=base_ns + 1_000_000_000),
        instrument,
    )
    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(100_000.0, usdt)],
            base_currency=usdt,
            default_leverage=Decimal("125"),
            reject_stop_orders=False,
            trade_execution=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(ticks)
        engine.add_strategy(strategy)
        engine.run()
        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        print("FILLS")
        print(fills.to_string())
        print("POSITIONS")
        print(positions.to_string())
        print("COUNTS", strategy.opened, strategy.closed)
        if strategy.opened != 1 or strategy.closed != 1:
            raise AssertionError("STOP_LIMIT bracket did not open and close exactly once")
        if not engine.portfolio.is_flat(instrument.id):
            raise AssertionError("STOP_LIMIT bracket did not end flat")
        print("authoritative STOP_LIMIT bracket smoke OK")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
