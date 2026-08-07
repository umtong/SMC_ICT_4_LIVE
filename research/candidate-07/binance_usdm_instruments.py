"""Minimal Binance USD-M perpetual definitions for frozen cross-symbol research."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.model import CryptoPerpetual
from nautilus_trader.model import Currency
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol
from nautilus_trader.model import Venue


@dataclass(frozen=True, slots=True)
class ContractGrid:
    price_increment: str
    size_increment: str

    @property
    def price_precision(self) -> int:
        return max(0, -Decimal(self.price_increment).as_tuple().exponent)

    @property
    def size_precision(self) -> int:
        return max(0, -Decimal(self.size_increment).as_tuple().exponent)


# BTC matches NautilusTrader 1.230.0 TestInstrumentProvider. ETH matches the
# official CryptoPerpetual ETHUSDT-PERP example. The remaining project symbols
# are declared for later untouched portability screens; their observed archive
# grids are validated before use by ``validate_observed_grid``.
CONTRACT_GRIDS = {
    "BTCUSDT": ContractGrid("0.1", "0.001"),
    "ETHUSDT": ContractGrid("0.01", "0.001"),
    "SOLUSDT": ContractGrid("0.001", "0.1"),
    "XRPUSDT": ContractGrid("0.0001", "0.1"),
}


def validate_observed_grid(
    *,
    symbol: str,
    prices: list[Decimal],
    quantities: list[Decimal],
) -> None:
    """Fail closed when raw trades do not lie on the declared venue grid."""
    grid = CONTRACT_GRIDS[symbol.upper()]
    price_step = Decimal(grid.price_increment)
    size_step = Decimal(grid.size_increment)
    if not prices or not quantities:
        raise ValueError("observed price and quantity samples must not be empty")
    if any(price % price_step != 0 for price in prices):
        raise RuntimeError(f"{symbol} raw prices violate {price_step} grid")
    if any(quantity % size_step != 0 for quantity in quantities):
        raise RuntimeError(f"{symbol} raw quantities violate {size_step} grid")


def binance_usdm_perpetual(symbol: str) -> CryptoPerpetual:
    """Return a non-binding-limit linear perpetual with account fee rates."""
    symbol = symbol.upper()
    if symbol not in CONTRACT_GRIDS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    if not symbol.endswith("USDT"):
        raise ValueError(f"expected a USDT quote symbol: {symbol}")
    base = symbol.removesuffix("USDT")
    grid = CONTRACT_GRIDS[symbol]
    return CryptoPerpetual(
        instrument_id=InstrumentId(
            Symbol(f"{symbol}-PERP"),
            Venue("BINANCE"),
        ),
        raw_symbol=Symbol(symbol),
        base_currency=Currency.from_str(base),
        quote_currency=Currency.from_str("USDT"),
        settlement_currency=Currency.from_str("USDT"),
        is_inverse=False,
        price_precision=grid.price_precision,
        size_precision=grid.size_precision,
        price_increment=Price.from_str(grid.price_increment),
        size_increment=Quantity.from_str(grid.size_increment),
        ts_event=0,
        ts_init=0,
        min_quantity=Quantity.from_str(grid.size_increment),
        margin_init=Decimal("0.0500"),
        margin_maint=Decimal("0.0250"),
        maker_fee=Decimal("0.000200"),
        taker_fee=Decimal("0.000180"),
        info={
            "source": "frozen project contract grid",
            "arbitrary_notional_cap": False,
            "max_quantity_cap": False,
        },
    )


__all__ = [
    "CONTRACT_GRIDS",
    "ContractGrid",
    "binance_usdm_perpetual",
    "validate_observed_grid",
]
