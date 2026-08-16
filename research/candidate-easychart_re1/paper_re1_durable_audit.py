"""Durable JSONL decision/execution audit for RE1 paper operation.

Batch backtests retain the complete in-memory event list.  A long-running paper
node must additionally survive process loss and bound memory use.  This mixin
appends every decision and execution event to a line-buffered JSONL file and
atomically refreshes a compact state snapshot for critical lifecycle events.
The snapshot is evidence, not a license to reconstruct an unknown live trade;
the paper strategy still flattens reconciled exposure after restart.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
from typing import Any


PAPER_EVENT_LOG_ENV = "EASYCHART_PAPER_EVENT_LOG"
PAPER_STATE_SNAPSHOT_ENV = "EASYCHART_PAPER_STATE_SNAPSHOT"
DEFAULT_EVENT_LOG = Path(".state/candidate-easychart_re1/paper_events.jsonl")
DEFAULT_STATE_SNAPSHOT = Path(".state/candidate-easychart_re1/paper_state.json")
EVENT_MEMORY_LIMIT = 50_000
EVENT_MEMORY_RETAIN = 25_000
CRITICAL_EVENT_PREFIXES = (
    "submitted",
    "arbitration_selected",
    "order_",
    "position_",
    "protective",
    "emergency_",
    "entry_terminal",
    "trade_slot_",
    "startup_reconciliation",
    "live_bar_coherence_fault",
)


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return str(value)


class DurablePaperAuditMixin:
    """Persist the existing strategy event contract without changing decisions."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._paper_event_path = Path(
            os.environ.get(PAPER_EVENT_LOG_ENV, str(DEFAULT_EVENT_LOG)),
        )
        self._paper_state_path = Path(
            os.environ.get(
                PAPER_STATE_SNAPSHOT_ENV,
                str(DEFAULT_STATE_SNAPSHOT),
            ),
        )
        self._paper_event_path.parent.mkdir(parents=True, exist_ok=True)
        self._paper_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._paper_event_stream = self._paper_event_path.open(
            "a",
            encoding="utf-8",
            buffering=1,
        )

    @staticmethod
    def _critical_kind(kind: str) -> bool:
        return kind.startswith(CRITICAL_EVENT_PREFIXES)

    def _write_state_snapshot(self, latest_event: dict[str, Any]) -> None:
        plan = getattr(self, "active_plan", None)
        record = {
            "latest_event": latest_event,
            "active_plan": None if plan is None else asdict(plan),
            "active_instrument_id": (
                None
                if getattr(self, "active_instrument_id", None) is None
                else str(self.active_instrument_id)
            ),
            "active_entry_id": (
                None
                if getattr(self, "active_entry_id", None) is None
                else str(self.active_entry_id)
            ),
            "active_stop_id": (
                None
                if getattr(self, "active_stop_id", None) is None
                else str(self.active_stop_id)
            ),
            "active_target_id": (
                None
                if getattr(self, "active_target_id", None) is None
                else str(self.active_target_id)
            ),
            "protection_submitted": bool(
                getattr(self, "protection_submitted", False),
            ),
            "emergency_exit_requested": bool(
                getattr(self, "emergency_exit_requested", False),
            ),
            "live_data_halted": bool(
                getattr(self, "_live_data_halted", False),
            ),
            "last_live_processed_ts": int(
                getattr(self, "_last_live_processed_ts", 0),
            ),
        }
        temporary = self._paper_state_path.with_suffix(
            self._paper_state_path.suffix + ".tmp",
        )
        temporary.write_text(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                default=_json_default,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._paper_state_path)

    def _record(self, kind: str, **values: Any) -> None:
        super()._record(kind, **values)
        event = self.event_log[-1]
        self._paper_event_stream.write(
            json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                default=_json_default,
            )
            + "\n",
        )
        if self._critical_kind(kind):
            self._paper_event_stream.flush()
            os.fsync(self._paper_event_stream.fileno())
            self._write_state_snapshot(event)
        if len(self.event_log) > EVENT_MEMORY_LIMIT:
            del self.event_log[:-EVENT_MEMORY_RETAIN]

    def on_start(self) -> None:
        super().on_start()
        self._record(
            "durable_paper_audit_ready",
            event_log_path=str(self._paper_event_path),
            state_snapshot_path=str(self._paper_state_path),
            memory_limit=EVENT_MEMORY_LIMIT,
        )

    def on_stop(self) -> None:
        try:
            super().on_stop()
            self._record("durable_paper_audit_stop_complete")
        finally:
            self._paper_event_stream.flush()
            os.fsync(self._paper_event_stream.fileno())
            self._paper_event_stream.close()


__all__ = [
    "DurablePaperAuditMixin",
    "PAPER_EVENT_LOG_ENV",
    "PAPER_STATE_SNAPSHOT_ENV",
]
