"""Evidence-chain correction for the controlled parent-only lifecycle.

Protection submission is an execution side effect of a parent fill, not a new
scenario state. Before Nautilus emits POSITION_OPENED the scenario remains
ORDER_PENDING; later fill chunks remain POSITION_OPEN. This subclass changes
only ResearchEvent previous/next labels so the evidence chain reflects that
ordering without altering any order or trading behavior.
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
        if event_type == "PROTECTION_SUBMITTED" and self.active_trade is not None:
            state = str(self.active_trade.get("event_state", "ORDER_PENDING"))
            previous_state = state
            next_state = state
        super()._append_execution_event(
            event_type=event_type,
            reason_code=reason_code,
            ts_ns=ts_ns,
            previous_state=previous_state,
            next_state=next_state,
            reference_price=reference_price,
            details=details,
        )

    def _handle_parent_execution_error(self, event: Any, kind: str) -> None:
        super()._handle_parent_execution_error(event, kind)
        if self.active_trade is not None:
            self.active_trade["event_state"] = "ORDER_ERROR"


__all__ = ["EvidenceValidatedParentProtectedStrategy"]
