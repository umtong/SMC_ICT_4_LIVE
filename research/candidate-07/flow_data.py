"""Causal aggressor-flow data carried through NautilusTrader."""
from __future__ import annotations

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId


class AggressorFlow(Data):
    """One completed minute of Binance taker-buy and total base volume."""

    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        total_volume: float,
        taker_buy_volume: float,
        ts_event: int,
        ts_init: int,
    ) -> None:
        if total_volume < 0.0:
            raise ValueError("total_volume must be non-negative")
        tolerance = max(1e-12, total_volume * 1e-9)
        if taker_buy_volume < -tolerance or taker_buy_volume > total_volume + tolerance:
            raise ValueError("taker_buy_volume must lie inside total_volume")
        if ts_event < 0 or ts_init < 0:
            raise ValueError("timestamps must be non-negative")
        self.instrument_id = instrument_id
        self.total_volume = float(total_volume)
        self.taker_buy_volume = float(taker_buy_volume)
        self._ts_event = int(ts_event)
        self._ts_init = int(ts_init)

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init


__all__ = ["AggressorFlow"]
