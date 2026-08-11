"""Frozen research contracts for the four-project universe."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Contract:
    symbol: str
    base: str
    price_precision: int
    tick_size: float
    size_precision: int
    size_increment: str
    min_quantity: float
    min_notional: float
    max_quantity: str
    min_price: str
    max_price: str

CONTRACTS = {
    "BTCUSDT": Contract("BTCUSDT", "BTC", 1, 0.1, 3, "0.001", 0.001, 10.0, "1000000.000", "0.1", "2000000.0"),
    "ETHUSDT": Contract("ETHUSDT", "ETH", 2, 0.01, 3, "0.001", 0.001, 20.0, "10000.000", "39.86", "306177.00"),
    "SOLUSDT": Contract("SOLUSDT", "SOL", 4, 0.01, 2, "0.01", 0.01, 5.0, "1000000.00", "0.4200", "6857.0000"),
    "XRPUSDT": Contract("XRPUSDT", "XRP", 4, 0.0001, 1, "0.1", 0.1, 5.0, "1000000.0", "0.0143", "100000.0000"),
}
