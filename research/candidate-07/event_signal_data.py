"""Immutable causal trade-signal payloads for NautilusTrader replay."""
from __future__ import annotations

from nautilus_trader.model.data import Data
from nautilus_trader.model.identifiers import ClientId, InstrumentId


EVENT_SIGNAL_CLIENT_ID = ClientId("CANDIDATE07-EVENT-SIGNAL")


class CausalTradeSignal(Data):
    """A completed scenario plan delivered strictly after its signal bar."""

    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        scenario_id: str,
        direction: str,
        entry_reference: float,
        stop_price: float,
        target_price: float,
        expected_rr: float,
        source_pool_id: str,
        signal_kind: str,
        details_json: str,
        observed_time_ns: int,
        ts_event: int,
        ts_init: int,
    ) -> None:
        if direction not in {"LONG", "SHORT"}:
            raise ValueError(f"unsupported signal direction: {direction}")
        if not scenario_id:
            raise ValueError("scenario_id must not be empty")
        if entry_reference <= 0.0 or stop_price <= 0.0 or target_price <= 0.0:
            raise ValueError("signal prices must be positive")
        if expected_rr <= 0.0:
            raise ValueError("expected_rr must be positive")
        if observed_time_ns <= 0 or ts_event <= 0 or ts_init <= 0:
            raise ValueError("signal timestamps must be positive")
        if ts_event < observed_time_ns or ts_init < ts_event:
            raise ValueError("signal timestamps are not causal")
        self.instrument_id = instrument_id
        self.scenario_id = scenario_id
        self.direction = direction
        self.entry_reference = float(entry_reference)
        self.stop_price = float(stop_price)
        self.target_price = float(target_price)
        self.expected_rr = float(expected_rr)
        self.source_pool_id = source_pool_id
        self.signal_kind = signal_kind
        self.details_json = details_json
        self.observed_time_ns = int(observed_time_ns)
        self._ts_event = int(ts_event)
        self._ts_init = int(ts_init)

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init


__all__ = ["CausalTradeSignal", "EVENT_SIGNAL_CLIENT_ID"]
