"""Frozen Nautilus instrument contracts for Candidate 05 research symbols.

The BTC contract is deliberately identical to the v26 baseline so that runner
generalization can be validated as a pure implementation change. The remaining
contracts were frozen from Binance's official USD-M exchange-info response
obtained through its official testnet endpoint after production endpoints
returned HTTP 451 from the GitHub-hosted runner region.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class InstrumentContract:
    symbol: str
    base_currency_code: str
    price_precision: int
    price_increment: str
    size_precision: int
    size_increment: str
    min_quantity: str
    max_quantity: str
    min_notional: float
    min_price: str
    max_price: str
    metadata_source: str

    @property
    def instrument_id(self) -> str:
        return f"{self.symbol}-PERP.BINANCE"

    @property
    def bar_type(self) -> str:
        return f"{self.instrument_id}-1-MINUTE-LAST-EXTERNAL"

    def manifest_values(self) -> dict[str, object]:
        return asdict(self)


_BTC_BASELINE_SOURCE = "FROZEN_CANDIDATE_05_V26"
_BINANCE_TESTNET_SOURCE = (
    "BINANCE_OFFICIAL_USDM_TESTNET_EXCHANGE_INFO_2026-08-06"
)

_CONTRACTS = {
    # Do not update these BTC values from a later exchangeInfo response. Exact
    # identity with the authoritative v26 evidence is the implementation-control
    # condition for the multi-symbol runner.
    "BTCUSDT": InstrumentContract(
        symbol="BTCUSDT",
        base_currency_code="BTC",
        price_precision=1,
        price_increment="0.1",
        size_precision=3,
        size_increment="0.001",
        min_quantity="0.001",
        max_quantity="1000000.000",
        min_notional=10.0,
        min_price="0.1",
        max_price="2000000.0",
        metadata_source=_BTC_BASELINE_SOURCE,
    ),
    "ETHUSDT": InstrumentContract(
        symbol="ETHUSDT",
        base_currency_code="ETH",
        price_precision=2,
        price_increment="0.01",
        size_precision=3,
        size_increment="0.001",
        min_quantity="0.001",
        max_quantity="10000.000",
        min_notional=20.0,
        min_price="39.86",
        max_price="306177.00",
        metadata_source=_BINANCE_TESTNET_SOURCE,
    ),
    "SOLUSDT": InstrumentContract(
        symbol="SOLUSDT",
        base_currency_code="SOL",
        price_precision=4,
        price_increment="0.0100",
        size_precision=2,
        size_increment="0.01",
        min_quantity="0.01",
        max_quantity="1000000.00",
        min_notional=5.0,
        min_price="0.4200",
        max_price="6857.0000",
        metadata_source=_BINANCE_TESTNET_SOURCE,
    ),
    "XRPUSDT": InstrumentContract(
        symbol="XRPUSDT",
        base_currency_code="XRP",
        price_precision=4,
        price_increment="0.0001",
        size_precision=1,
        size_increment="0.1",
        min_quantity="0.1",
        max_quantity="1000000.0",
        min_notional=5.0,
        min_price="0.0143",
        max_price="100000.0000",
        metadata_source=_BINANCE_TESTNET_SOURCE,
    ),
}

ALLOWED_SYMBOLS = tuple(_CONTRACTS)


def instrument_contract(symbol: str) -> InstrumentContract:
    normalized = str(symbol).strip().upper()
    try:
        return _CONTRACTS[normalized]
    except KeyError as exc:
        raise ValueError(
            f"unsupported symbol {symbol!r}; expected one of {ALLOWED_SYMBOLS}",
        ) from exc


__all__ = ["ALLOWED_SYMBOLS", "InstrumentContract", "instrument_contract"]
