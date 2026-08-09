"""Audited lifecycle coordinator for one global executable trading slot.

The coordinator distinguishes an unfilled new-entry intent from an open
position.  A successful fill transitions the single slot from ``ENTRY_INTENT``
to ``POSITION_OPEN``; it does not create a second unit.  Exit and reduction
orders never acquire this coordinator.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Any


ENTRY_INTENT = "ENTRY_INTENT"
POSITION_OPEN = "POSITION_OPEN"
POSITION_CLOSED_AWAIT_RELEASE = "POSITION_CLOSED_AWAIT_RELEASE"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    sequence: int
    ts_event: int
    action: str
    actor: str
    owner_after: str | None
    phase_after: str | None
    reason: str
    context: dict[str, Any]


class SharedAccountEntryCoordinator:
    """Serialize all new entries across strategy instances in one process."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._owner: str | None = None
        self._phase: str | None = None
        self._sequence = 0
        self._events: list[LifecycleEvent] = []

    @property
    def owner(self) -> str | None:
        with self._lock:
            return self._owner

    @property
    def phase(self) -> str | None:
        with self._lock:
            return self._phase

    def reset(self) -> None:
        with self._lock:
            self._owner = None
            self._phase = None
            self._sequence = 0
            self._events.clear()

    def _record(
        self,
        *,
        ts_event: int,
        action: str,
        actor: str,
        reason: str,
        context: dict[str, Any] | None,
    ) -> None:
        self._sequence += 1
        self._events.append(
            LifecycleEvent(
                sequence=self._sequence,
                ts_event=int(ts_event),
                action=action,
                actor=actor,
                owner_after=self._owner,
                phase_after=self._phase,
                reason=reason,
                context=dict(context or {}),
            ),
        )

    def acquire_entry_intent(
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
                self._phase = ENTRY_INTENT
                self._record(
                    ts_event=ts_event,
                    action="ENTRY_INTENT_ACQUIRED",
                    actor=owner,
                    reason=reason,
                    context=context,
                )
                return True
            if self._owner == owner and self._phase == ENTRY_INTENT:
                self._record(
                    ts_event=ts_event,
                    action="ENTRY_INTENT_REENTERED",
                    actor=owner,
                    reason=reason,
                    context=context,
                )
                return True
            self._record(
                ts_event=ts_event,
                action="ENTRY_INTENT_CONFLICT",
                actor=owner,
                reason=reason,
                context=context,
            )
            return False

    def position_opened(
        self,
        *,
        owner: str,
        ts_event: int,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            if self._owner != owner or self._phase != ENTRY_INTENT:
                self._record(
                    ts_event=ts_event,
                    action="POSITION_OPEN_MISMATCH",
                    actor=owner,
                    reason=reason,
                    context=context,
                )
                return False
            self._phase = POSITION_OPEN
            self._record(
                ts_event=ts_event,
                action="POSITION_OPENED",
                actor=owner,
                reason=reason,
                context=context,
            )
            return True

    def position_closed(
        self,
        *,
        owner: str,
        ts_event: int,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            if self._owner != owner or self._phase != POSITION_OPEN:
                self._record(
                    ts_event=ts_event,
                    action="POSITION_CLOSE_MISMATCH",
                    actor=owner,
                    reason=reason,
                    context=context,
                )
                return False
            self._phase = POSITION_CLOSED_AWAIT_RELEASE
            self._record(
                ts_event=ts_event,
                action="POSITION_CLOSED",
                actor=owner,
                reason=reason,
                context=context,
            )
            return True

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

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(event) for event in self._events]

    def audit(self) -> dict[str, Any]:
        """Independently replay the full intent/position lifecycle."""
        owner: str | None = None
        phase: str | None = None
        max_entry_intents = 0
        max_open_positions = 0
        max_entry_plus_positions = 0
        acquisitions = 0
        conflicts = 0
        opened = 0
        closed = 0
        releases = 0
        mismatches = 0
        violations: list[dict[str, Any]] = []

        for event in self.events():
            action = event["action"]
            actor = event["actor"]
            before = {"owner": owner, "phase": phase}
            if action == "ENTRY_INTENT_ACQUIRED":
                acquisitions += 1
                if owner is not None:
                    violations.append({"type": "ACQUIRE_WHILE_OCCUPIED", "before": before, "event": event})
                owner = actor
                phase = ENTRY_INTENT
            elif action == "ENTRY_INTENT_REENTERED":
                if owner != actor or phase != ENTRY_INTENT:
                    violations.append({"type": "INVALID_REENTRY", "before": before, "event": event})
            elif action == "ENTRY_INTENT_CONFLICT":
                conflicts += 1
                if owner is None:
                    violations.append({"type": "CONFLICT_WHILE_IDLE", "before": before, "event": event})
            elif action == "POSITION_OPENED":
                opened += 1
                if owner != actor or phase != ENTRY_INTENT:
                    violations.append({"type": "POSITION_OPEN_WITHOUT_INTENT", "before": before, "event": event})
                owner = actor
                phase = POSITION_OPEN
            elif action == "POSITION_CLOSED":
                closed += 1
                if owner != actor or phase != POSITION_OPEN:
                    violations.append({"type": "POSITION_CLOSE_WITHOUT_POSITION", "before": before, "event": event})
                owner = actor
                phase = POSITION_CLOSED_AWAIT_RELEASE
            elif action == "SLOT_RELEASED":
                releases += 1
                if owner != actor:
                    violations.append({"type": "RELEASE_BY_NONOWNER", "before": before, "event": event})
                owner = None
                phase = None
            elif action in {
                "POSITION_OPEN_MISMATCH",
                "POSITION_CLOSE_MISMATCH",
                "RELEASE_MISMATCH",
            }:
                mismatches += 1
            else:
                violations.append({"type": "UNKNOWN_ACTION", "before": before, "event": event})

            entry_intents = int(phase == ENTRY_INTENT)
            open_positions = int(phase == POSITION_OPEN)
            total = entry_intents + open_positions
            max_entry_intents = max(max_entry_intents, entry_intents)
            max_open_positions = max(max_open_positions, open_positions)
            max_entry_plus_positions = max(max_entry_plus_positions, total)
            if total > 1:
                violations.append({"type": "GLOBAL_SUM_EXCEEDED_ONE", "before": before, "event": event})

            if event["owner_after"] != owner or event["phase_after"] != phase:
                violations.append(
                    {
                        "type": "RECORDED_STATE_DIFFERS_FROM_REPLAY",
                        "before": before,
                        "event": event,
                        "replayed_after": {"owner": owner, "phase": phase},
                    },
                )

        idle_at_end = owner is None and phase is None
        return {
            "events": len(self._events),
            "acquisitions": acquisitions,
            "conflicts": conflicts,
            "positions_opened": opened,
            "positions_closed": closed,
            "releases": releases,
            "mismatches": mismatches,
            "max_unfilled_entry_intents_replayed": max_entry_intents,
            "max_open_positions_replayed": max_open_positions,
            "max_entry_intents_plus_positions_replayed": max_entry_plus_positions,
            "owner_at_end": owner,
            "phase_at_end": phase,
            "idle_at_end": idle_at_end,
            "violations": violations,
            "audit_pass": (
                max_entry_intents <= 1
                and max_open_positions <= 1
                and max_entry_plus_positions <= 1
                and mismatches == 0
                and not violations
                and idle_at_end
            ),
        }


SHARED_ACCOUNT_ENTRY_COORDINATOR = SharedAccountEntryCoordinator()


def reset_shared_account_entry_coordinator() -> None:
    SHARED_ACCOUNT_ENTRY_COORDINATOR.reset()


__all__ = [
    "ENTRY_INTENT",
    "POSITION_CLOSED_AWAIT_RELEASE",
    "POSITION_OPEN",
    "LifecycleEvent",
    "SHARED_ACCOUNT_ENTRY_COORDINATOR",
    "SharedAccountEntryCoordinator",
    "reset_shared_account_entry_coordinator",
]
