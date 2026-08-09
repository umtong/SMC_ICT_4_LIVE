"""Strict audited process-global entry slot."""
from __future__ import annotations

from typing import Any

from global_entry_slot import GlobalEntrySlotCoordinator


class StrictGlobalEntrySlotCoordinator(GlobalEntrySlotCoordinator):
    """Treat every ownership mismatch as an audit failure."""

    def audit(self) -> dict[str, Any]:
        result = super().audit()
        events = self.events()
        release_mismatches = sum(event["action"] == "RELEASE_MISMATCH" for event in events)
        result["release_mismatches"] = release_mismatches
        result["audit_pass"] = bool(result["audit_pass"]) and release_mismatches == 0
        return result


STRICT_GLOBAL_ENTRY_SLOT = StrictGlobalEntrySlotCoordinator()


def reset_strict_global_entry_slot() -> None:
    STRICT_GLOBAL_ENTRY_SLOT.reset()


__all__ = [
    "STRICT_GLOBAL_ENTRY_SLOT",
    "StrictGlobalEntrySlotCoordinator",
    "reset_strict_global_entry_slot",
]
