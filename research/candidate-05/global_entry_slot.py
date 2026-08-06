"""Process-global executable-entry slot for one shared Nautilus account.

The coordinator does not size, rank, fill or account for orders.  It only
serializes permission to create a new entry order across strategy instances in
the same BacktestNode process.  Position reductions and exits never acquire the
slot.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Any


@dataclass(frozen=True, slots=True)
class SlotEvent:
    sequence: int
    ts_event: int
    action: str
    owner: str
    current_owner: str | None
    reason: str
    context: dict[str, Any]


class GlobalEntrySlotCoordinator:
    def __init__(self) -> None:
        self._lock = RLock()
        self._owner: str | None = None
        self._events: list[SlotEvent] = []
        self._sequence = 0

    @property
    def owner(self) -> str | None:
        with self._lock:
            return self._owner

    def reset(self) -> None:
        with self._lock:
            self._owner = None
            self._events.clear()
            self._sequence = 0

    def _record(
        self,
        *,
        ts_event: int,
        action: str,
        owner: str,
        reason: str,
        context: dict[str, Any] | None,
    ) -> None:
        self._sequence += 1
        self._events.append(
            SlotEvent(
                sequence=self._sequence,
                ts_event=int(ts_event),
                action=action,
                owner=owner,
                current_owner=self._owner,
                reason=reason,
                context=dict(context or {}),
            ),
        )

    def acquire(
        self,
        *,
        owner: str,
        ts_event: int,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        if not owner:
            raise ValueError("owner must be non-empty")
        with self._lock:
            if self._owner is None:
                self._owner = owner
                self._record(
                    ts_event=ts_event,
                    action="ACQUIRE",
                    owner=owner,
                    reason=reason,
                    context=context,
                )
                return True
            if self._owner == owner:
                self._record(
                    ts_event=ts_event,
                    action="REENTER_OWNER",
                    owner=owner,
                    reason=reason,
                    context=context,
                )
                return True
            self._record(
                ts_event=ts_event,
                action="CONFLICT",
                owner=owner,
                reason=reason,
                context=context,
            )
            return False

    def release(
        self,
        *,
        owner: str,
        ts_event: int,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        if not owner:
            raise ValueError("owner must be non-empty")
        with self._lock:
            if self._owner != owner:
                self._record(
                    ts_event=ts_event,
                    action="RELEASE_MISMATCH",
                    owner=owner,
                    reason=reason,
                    context=context,
                )
                return False
            self._owner = None
            self._record(
                ts_event=ts_event,
                action="RELEASE",
                owner=owner,
                reason=reason,
                context=context,
            )
            return True

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(event) for event in self._events]

    def audit(self) -> dict[str, Any]:
        """Replay slot events independently of the live owner variable."""
        active: set[str] = set()
        max_active = 0
        violations: list[dict[str, Any]] = []
        conflicts = 0
        acquisitions = 0
        releases = 0
        for event in self.events():
            action = event["action"]
            owner = event["owner"]
            if action == "ACQUIRE":
                acquisitions += 1
                if active and owner not in active:
                    violations.append({"type": "OVERLAPPING_ACQUIRE", "event": event, "active_before": sorted(active)})
                active.add(owner)
            elif action == "REENTER_OWNER":
                if owner not in active:
                    violations.append({"type": "REENTER_WITHOUT_ACQUIRE", "event": event, "active_before": sorted(active)})
            elif action == "CONFLICT":
                conflicts += 1
                if not active:
                    violations.append({"type": "CONFLICT_WITH_NO_ACTIVE_OWNER", "event": event})
            elif action == "RELEASE":
                releases += 1
                if owner not in active:
                    violations.append({"type": "RELEASE_WITHOUT_ACTIVE_OWNER", "event": event, "active_before": sorted(active)})
                active.discard(owner)
            elif action == "RELEASE_MISMATCH":
                # A mismatch is diagnostic, not an unauthorized ownership change.
                pass
            max_active = max(max_active, len(active))
        return {
            "events": len(self._events),
            "acquisitions": acquisitions,
            "releases": releases,
            "conflicts": conflicts,
            "max_simultaneous_owners_replayed": max_active,
            "active_owners_at_end": sorted(active),
            "violations": violations,
            "audit_pass": max_active <= 1 and not violations and not active,
        }


GLOBAL_ENTRY_SLOT = GlobalEntrySlotCoordinator()


def reset_global_entry_slot() -> None:
    GLOBAL_ENTRY_SLOT.reset()


__all__ = [
    "GLOBAL_ENTRY_SLOT",
    "GlobalEntrySlotCoordinator",
    "SlotEvent",
    "reset_global_entry_slot",
]
