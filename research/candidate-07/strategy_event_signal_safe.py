"""Execution-event serialization for synchronous Nautilus bracket fills.

NautilusTrader may emit more than one ``PositionOpened`` callback for the same
NETTING lifecycle, or close a very short-lived position before the strategy has
recorded a distinct open transition.  This adapter changes no orders, fills,
prices, quantities, fees, funding, PnL or NAV.  It only keeps the append-only
research-event chain causal and idempotent.
"""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.events import PositionClosed

from strategy_event_signal import Candidate07EventSignalStrategy


class Candidate07SerializedEventStrategy(Candidate07EventSignalStrategy):
    """Candidate strategy with idempotent per-scenario execution transitions."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self._execution_state: dict[str, str] = {}

    def on_position_closed(self, event: PositionClosed) -> None:
        signal = self._active_signal
        if (
            signal is not None
            and self._execution_state.get(signal.scenario_id) == "ORDER_SUBMITTED"
            and self._position_open_ns is None
        ):
            # A closed position proves that the market parent filled even when
            # no unique PositionOpened transition was delivered first.
            self._position_open_ns = int(event.ts_event)
        super().on_position_closed(event)

    def _append_event(
        self,
        *,
        scenario_id: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        event_time_ns: int,
        reference_price: float,
        details: dict[str, Any],
    ) -> None:
        current = self._execution_state.get(scenario_id, "ENTRY_READY")

        if next_state == "POSITION_OPEN" and current == "POSITION_OPEN":
            self._diagnostics.append(
                {
                    "scenario_id": scenario_id,
                    "reason": "DUPLICATE_POSITION_OPEN_CALLBACK_IGNORED",
                    "ts_event_ns": int(event_time_ns),
                }
            )
            return
        if current in {"TERMINAL", "INVALIDATED"}:
            self._diagnostics.append(
                {
                    "scenario_id": scenario_id,
                    "reason": "POST_TERMINAL_EXECUTION_CALLBACK_IGNORED",
                    "ts_event_ns": int(event_time_ns),
                    "callback_next_state": next_state,
                    "current_state": current,
                }
            )
            return

        if next_state == "TERMINAL" and current == "ORDER_SUBMITTED":
            super()._append_event(
                scenario_id=scenario_id,
                previous_state="ORDER_SUBMITTED",
                next_state="POSITION_OPEN",
                reason_code="NAUTILUS_POSITION_OPEN_INFERRED_FROM_CLOSE",
                event_time_ns=int(event_time_ns),
                reference_price=float(reference_price),
                details={
                    "inferred": True,
                    "basis": "PositionClosed proves a filled opening parent",
                },
            )
            self._execution_state[scenario_id] = "POSITION_OPEN"
            current = "POSITION_OPEN"

        if previous_state != current:
            self._diagnostics.append(
                {
                    "scenario_id": scenario_id,
                    "reason": "EXECUTION_CALLBACK_STATE_RECONCILED",
                    "ts_event_ns": int(event_time_ns),
                    "declared_previous_state": previous_state,
                    "actual_previous_state": current,
                    "next_state": next_state,
                }
            )
            previous_state = current

        super()._append_event(
            scenario_id=scenario_id,
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason_code,
            event_time_ns=int(event_time_ns),
            reference_price=float(reference_price),
            details=details,
        )
        self._execution_state[scenario_id] = next_state


__all__ = ["Candidate07SerializedEventStrategy"]
