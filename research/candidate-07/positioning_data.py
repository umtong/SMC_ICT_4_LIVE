"""Causal Binance USD-M positioning data carried through NautilusTrader."""
from __future__ import annotations

from math import isfinite

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import ClientId, InstrumentId


POSITIONING_CLIENT_ID = ClientId("C07-POSITIONING")


class PositioningSnapshot(Data):
    """One completed five-minute Binance USD-M positioning snapshot."""

    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        open_interest: float,
        open_interest_value: float,
        top_trader_account_ratio: float | None,
        top_trader_position_ratio: float | None,
        global_long_short_ratio: float | None,
        taker_long_short_ratio: float | None,
        ts_event: int,
        ts_init: int,
    ) -> None:
        if open_interest <= 0.0 or not isfinite(open_interest):
            raise ValueError("open_interest must be finite and positive")
        if open_interest_value <= 0.0 or not isfinite(open_interest_value):
            raise ValueError("open_interest_value must be finite and positive")
        if ts_event < 0 or ts_init < 0:
            raise ValueError("timestamps must be non-negative")
        for name, value in (
            ("top_trader_account_ratio", top_trader_account_ratio),
            ("top_trader_position_ratio", top_trader_position_ratio),
            ("global_long_short_ratio", global_long_short_ratio),
            ("taker_long_short_ratio", taker_long_short_ratio),
        ):
            if value is not None and (value <= 0.0 or not isfinite(value)):
                raise ValueError(f"{name} must be finite and positive when present")
        self.instrument_id = instrument_id
        self.open_interest = float(open_interest)
        self.open_interest_value = float(open_interest_value)
        self.top_trader_account_ratio = (
            None if top_trader_account_ratio is None else float(top_trader_account_ratio)
        )
        self.top_trader_position_ratio = (
            None if top_trader_position_ratio is None else float(top_trader_position_ratio)
        )
        self.global_long_short_ratio = (
            None if global_long_short_ratio is None else float(global_long_short_ratio)
        )
        self.taker_long_short_ratio = (
            None if taker_long_short_ratio is None else float(taker_long_short_ratio)
        )
        self._ts_event = int(ts_event)
        self._ts_init = int(ts_init)

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init


__all__ = ["POSITIONING_CLIENT_ID", "PositioningSnapshot"]
