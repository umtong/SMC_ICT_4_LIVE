"""Frozen Binance USD-M instrument definitions for the project universe."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity


@dataclass(frozen=True, slots=True)
class Contract:
    symbol: str
    base: str
    price_increment: str
    size_increment: str
    min_quantity: str
    max_quantity: str
    min_notional: str
    min_price: str
    max_price: str


CONTRACTS: dict[str, Contract] = {
    "BTCUSDT": Contract("BTCUSDT", "BTC", "0.1", "0.001", "0.001", "1000000.000", "10", "0.1", "2000000.0"),
    "ETHUSDT": Contract("ETHUSDT", "ETH", "0.01", "0.001", "0.001", "10000.000", "20", "0.01", "306177.00"),
    "SOLUSDT": Contract("SOLUSDT", "SOL", "0.01", "0.01", "0.01", "1000000.00", "5", "0.01", "6857.00"),
    "XRPUSDT": Contract("XRPUSDT", "XRP", "0.0001", "0.1", "0.1", "1000000.0", "5", "0.0001", "100000.0000"),
}


def _precision(value: str) -> int:
    return len(value.partition(".")[2].rstrip("0")) if "." in value else 0


def make_instrument(symbol: str) -> CryptoPerpetual:
    contract = CONTRACTS[symbol]
    base = Currency.from_str(contract.base)
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{symbol}-PERP"), Venue("BINANCE")),
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=_precision(contract.price_increment),
        price_increment=Price.from_str(contract.price_increment),
        size_precision=_precision(contract.size_increment),
        size_increment=Quantity.from_str(contract.size_increment),
        max_quantity=Quantity.from_str(contract.max_quantity),
        min_quantity=Quantity.from_str(contract.min_quantity),
        max_notional=None,
        min_notional=Money(Decimal(contract.min_notional), usdt),
        max_price=Price.from_str(contract.max_price),
        min_price=Price.from_str(contract.min_price),
        margin_init=Decimal("0.01"),
        margin_maint=Decimal("0.005"),
        maker_fee=Decimal("0.00075"),
        taker_fee=Decimal("0.00075"),
        ts_event=0,
        ts_init=0,
    )
