#!/usr/bin/env python3
"""Portfolio-wide one-entry coordinator for four NautilusTrader strategies.

Each instrument retains its own causal signal state, bars, targets and brackets,
but all strategy instances share one coordinator keyed to the backtest/live
session.  A strategy reserves the global entry slot before submitting a new
entry.  Other instruments cannot submit while a reservation or position exists.
Exits, reductions and protective orders are not blocked.

This module does not match orders or calculate PnL.  NautilusTrader remains the
sole owner of orders, fills, positions, fees, margin, liquidation and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nautilus_trader.model.identifiers import InstrumentId

from nt_rich_signal_strategy import RichSignalConfig
from nt_rich_signal_strategy import RichSignalStrategy


@dataclass(slots=True)
class _GlobalEntryState:
    pending_instrument: str | None = None
    active_instrument: str | None = None
    reservations: int = 0
    blocked_entries: int = 0
    maximum_live_entries: int = 0
    invariant_violations: int = 0

    def live_count(self) -> int:
        return int(self.pending_instrument is not None) + int(
            self.active_instrument is not None
        )

    def observe(self) -> None:
        count = self.live_count()
        self.maximum_live_entries = max(self.maximum_live_entries, count)
        if count > 1:
            self.invariant_violations += 1


_COORDINATORS: dict[str, _GlobalEntryState] = {}


def reset_global_coordinator(key: str) -> None:
    """Reset one execution session before strategies are instantiated."""

    _COORDINATORS[key] = _GlobalEntryState()


def coordinator_snapshot(key: str) -> dict[str, Any]:
    state = _COORDINATORS.setdefault(key, _GlobalEntryState())
    return {
        "pending_instrument": state.pending_instrument,
        "active_instrument": state.active_instrument,
        "reservations": state.reservations,
        "blocked_entries": state.blocked_entries,
        "maximum_live_entries": state.maximum_live_entries,
        "invariant_violations": state.invariant_violations,
        "live_count": state.live_count(),
    }


class GlobalRichSignalConfig(RichSignalConfig, frozen=True):
    global_instrument_ids: tuple[str, ...] = ()
    coordinator_key: str = "candidate-04-global"


class GlobalRichSignalStrategy(RichSignalStrategy):
    """A single-instrument strategy obeying one portfolio-wide entry slot."""

    def _coordinator(self) -> _GlobalEntryState:
        return _COORDINATORS.setdefault(
            str(self.config.coordinator_key),
            _GlobalEntryState(),
        )

    def _instrument_text(self) -> str:
        return str(self.config.instrument_id)

    def _global_flat(self) -> bool:
        identifiers = self.config.global_instrument_ids or (
            self._instrument_text(),
        )
        return all(
            self.portfolio.is_flat(InstrumentId.from_str(value))
            for value in identifiers
        )

    def _global_event(
        self,
        event_type: str,
        scenario: str,
        row: dict[str, float | int],
        details: dict[str, Any] | None = None,
    ) -> None:
        state = self._coordinator()
        state.observe()
        self._event(
            event_type,
            scenario,
            row,
            {
                "instrument_id": self._instrument_text(),
                "coordinator_key": str(self.config.coordinator_key),
                "global_state": coordinator_snapshot(
                    str(self.config.coordinator_key)
                ),
                **(details or {}),
            },
        )

    def on_start(self) -> None:
        super().on_start()
        self._global_event(
            "GLOBAL_COORDINATOR_REGISTERED",
            "CONTROL",
            self.bars[-1] if self.bars else {"ts": 0, "close": 0.0},
            {"global_instrument_ids": list(self.config.global_instrument_ids)},
        )

    def _submit_signal(
        self,
        signal: dict[str, Any],
        row: dict[str, float | int],
    ) -> bool:
        state = self._coordinator()
        instrument = self._instrument_text()
        occupied = (
            state.pending_instrument is not None
            or state.active_instrument is not None
            or not self._global_flat()
        )
        if occupied:
            state.blocked_entries += 1
            self._global_event(
                "GLOBAL_NEW_ENTRY_BLOCKED",
                str(signal.get("scenario", "UNKNOWN")),
                row,
                {
                    "signal": signal,
                    "reason": "another pending entry or open position exists",
                },
            )
            return False

        state.pending_instrument = instrument
        state.reservations += 1
        state.observe()
        if state.invariant_violations:
            state.pending_instrument = None
            raise RuntimeError("global entry invariant violated during reservation")
        self._global_event(
            "GLOBAL_ENTRY_RESERVED",
            str(signal.get("scenario", "UNKNOWN")),
            row,
            {"signal": signal},
        )
        submitted = super()._submit_signal(signal, row)
        if not submitted and state.pending_instrument == instrument:
            state.pending_instrument = None
            state.observe()
            self._global_event(
                "GLOBAL_ENTRY_RESERVATION_RELEASED",
                str(signal.get("scenario", "UNKNOWN")),
                row,
                {"reason": "instrument strategy rejected submission"},
            )
        return submitted

    def on_position_opened(self, event: Any) -> None:
        state = self._coordinator()
        instrument = self._instrument_text()
        if state.pending_instrument not in (None, instrument):
            state.invariant_violations += 1
            raise RuntimeError("position opened outside its global reservation")
        state.pending_instrument = None
        state.active_instrument = instrument
        state.observe()
        if state.invariant_violations:
            raise RuntimeError("more than one global entry became live")
        super().on_position_opened(event)
        self._global_event(
            "GLOBAL_POSITION_OPENED",
            "CONTROL",
            self.bars[-1],
            {"event": str(event)},
        )

    def on_position_closed(self, event: Any) -> None:
        super().on_position_closed(event)
        state = self._coordinator()
        instrument = self._instrument_text()
        if state.active_instrument == instrument:
            state.active_instrument = None
        if state.pending_instrument == instrument:
            state.pending_instrument = None
        state.observe()
        self._global_event(
            "GLOBAL_POSITION_CLOSED",
            "CONTROL",
            self.bars[-1],
            {"event": str(event)},
        )

    def on_order_rejected(self, event: Any) -> None:
        super().on_order_rejected(event)
        self._release_pending_if_flat("GLOBAL_ENTRY_REJECTED", event)

    def on_order_canceled(self, event: Any) -> None:
        super().on_order_canceled(event)
        self._release_pending_if_flat("GLOBAL_ENTRY_CANCELED", event)

    def _release_pending_if_flat(self, event_type: str, event: Any) -> None:
        state = self._coordinator()
        instrument = self._instrument_text()
        if self._global_flat() and state.pending_instrument == instrument:
            state.pending_instrument = None
            state.observe()
            self._global_event(
                event_type,
                "CONTROL",
                self.bars[-1] if self.bars else {"ts": 0, "close": 0.0},
                {"event": str(event)},
            )

    def on_stop(self) -> None:
        state = self._coordinator()
        instrument = self._instrument_text()
        if state.pending_instrument == instrument:
            state.pending_instrument = None
        if state.active_instrument == instrument and self._global_flat():
            state.active_instrument = None
        state.observe()
        self._global_event(
            "GLOBAL_COORDINATOR_FINAL",
            "CONTROL",
            self.bars[-1] if self.bars else {"ts": 0, "close": 0.0},
        )
        super().on_stop()


__all__ = [
    "GlobalRichSignalConfig",
    "GlobalRichSignalStrategy",
    "coordinator_snapshot",
    "reset_global_coordinator",
]
