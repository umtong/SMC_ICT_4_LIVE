"""Final strict shared-account lifecycle coordinator."""
from __future__ import annotations

from typing import Any

from global_entry_slot_v3 import ENTRY_INTENT
from global_entry_slot_v3 import POSITION_CLOSED_AWAIT_RELEASE
from global_entry_slot_v3 import SharedAccountEntryCoordinator


class FinalSharedAccountEntryCoordinator(SharedAccountEntryCoordinator):
    """Reject releases which would hide a still-open position lifecycle."""

    def release(
        self,
        *,
        owner: str,
        ts_event: int,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            if self._owner != owner:
                self._record(
                    ts_event=ts_event,
                    action="RELEASE_MISMATCH",
                    actor=owner,
                    reason=reason,
                    context=context,
                )
                return False
            if self._phase not in {ENTRY_INTENT, POSITION_CLOSED_AWAIT_RELEASE}:
                self._record(
                    ts_event=ts_event,
                    action="RELEASE_PHASE_MISMATCH",
                    actor=owner,
                    reason=reason,
                    context={"phase_before": self._phase, **(context or {})},
                )
                return False
            self._owner = None
            self._phase = None
            self._record(
                ts_event=ts_event,
                action="SLOT_RELEASED",
                actor=owner,
                reason=reason,
                context=context,
            )
            return True

    def audit(self) -> dict[str, Any]:
        result = super().audit()
        release_phase_mismatches = sum(
            event["action"] == "RELEASE_PHASE_MISMATCH"
            for event in self.events()
        )
        result["release_phase_mismatches"] = release_phase_mismatches
        result["audit_pass"] = bool(result["audit_pass"]) and release_phase_mismatches == 0
        return result


FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR = FinalSharedAccountEntryCoordinator()


def reset_final_shared_account_entry_coordinator() -> None:
    FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.reset()


__all__ = [
    "FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR",
    "FinalSharedAccountEntryCoordinator",
    "reset_final_shared_account_entry_coordinator",
]
