"""Completed Binance USD-M index-price reference carried through NautilusTrader."""
from __future__ import annotations

from math import isfinite

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import ClientId, InstrumentId


INDEX_REFERENCE_CLIENT_ID = ClientId("C07-INDEX-PRICE")


class IndexPriceReference(Data):
    """One completed one-minute Binance index-price bar."""

    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        open: float,
        high: float,
        low: float,
        close: float,
        ts_event: int,
        ts_init: int,
    ) -> None:
        values = (open, high, low, close)
        if any(value <= 0.0 or not isfinite(value) for value in values):
            raise ValueError("index-price OHLC values must be finite and positive")
        if high < max(open, close) or low > min(open, close) or high < low:
            raise ValueError("index-price OHLC values are inconsistent")
        if ts_event < 0 or ts_init < 0:
            raise ValueError("timestamps must be non-negative")
        self.instrument_id = instrument_id
        self.open = float(open)
        self.high = float(high)
        self.low = float(low)
        self.close = float(close)
        self._ts_event = int(ts_event)
        self._ts_init = int(ts_init)

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init


__all__ = ["INDEX_REFERENCE_CLIENT_ID", "IndexPriceReference"]
