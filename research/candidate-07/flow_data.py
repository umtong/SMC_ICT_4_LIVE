"""Causal aggressor-flow data carried through NautilusTrader."""
from __future__ import annotations

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId


class AggressorFlow(Data):
    """One completed minute of Binance taker-buy and total base volume.

    The values come directly from the checksum-verified Binance kline archive.
    This class contains no signal or execution behavior; it only gives the
    NautilusTrader data engine a typed event which is available to the strategy
    immediately before the corresponding completed bar.
    """

    instrument_id: InstrumentId
    total_volume: float
    taker_buy_volume: float


__all__ = ["AggressorFlow"]
