"""Evidence-chain correction for the controlled parent-only lifecycle.

Nautilus can emit parent fills, position events, protective-order events and
rejections at the same venue timestamp. These callbacks are execution side
effects, not extra market-scenario states. This subclass derives every execution
event's ``previous_state`` from the scenario's actual active state. Protection
submission is a self-transition; genuine state-changing events update the active
state after they are recorded. No order or trading behavior is changed.
"""

from __future__ import annotations

from typing import Any

from c10_flow_parent_execution import ParentProtectedFlowCandidate10Strategy


class EvidenceValidatedParentProtectedStrategy(
    ParentProtectedFlowCandidate10Strategy,
):
    def _append_execution_event(
        self,
        *,
        event_type: str,
        reason_code: str,
        ts_ns: int,
        previous_state: str,
        next_state: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.active_trade is not None:
            current_state = str(
                self.active_trade.get("event_state", "ORDER_PENDING"),
            )
            # ORDER_SUBMITTED is the first execution event and must retain the
            # detector's ENTRY_READY -> ORDER_PENDING transition. Every later
            # execution callback starts from the state actually reached so far.
            if event_type != "ORDER_SUBMITTED":
                previous_state = current_state
            if event_type == "PROTECTION_SUBMITTED":
                next_state = current_state

        super()._append_execution_event(
            event_type=event_type,
            reason_code=reason_code,
            ts_ns=ts_ns,
            previous_state=previous_state,
            next_state=next_state,
            reference_price=reference_price,
            details=details,
        )

        if self.active_trade is not None and event_type != "PROTECTION_SUBMITTED":
            self.active_trade["event_state"] = next_state


__all__ = ["EvidenceValidatedParentProtectedStrategy"]
