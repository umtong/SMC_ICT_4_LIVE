"""Idempotency contract for duplicate Nautilus order-failure callbacks.

A byte-identical rejection/denial callback can be delivered more than once at
shutdown.  Replaying the same callback must not create two identical
ResearchEvent objects, increment failure counts twice, or rerun local cleanup.
The first callback remains fully authoritative; only an exact duplicate for the
same strategy instance is ignored.
"""
from __future__ import annotations

from functools import wraps
from typing import Any

import strategy_v2 as _v2


def install() -> None:
    cls = _v2.LiquidityResponseRetraceStrategy
    current = cls._order_failure
    if getattr(current, "_candidate05_exact_failure_idempotency", False):
        return

    @wraps(current)
    def idempotent(self: Any, event: Any, event_type: str) -> None:
        ts = int(getattr(event, "ts_event", self.bars[-1]["ts"] if self.bars else 0))
        key = (
            self.current_scenario_id,
            ts,
            str(event_type),
            str(event),
        )
        seen = getattr(self, "_candidate05_seen_order_failures", None)
        if seen is None:
            seen = set()
            setattr(self, "_candidate05_seen_order_failures", seen)
            self.diagnostics.setdefault("duplicate_order_failure_callbacks_ignored", 0)
        if key in seen:
            self.diagnostics["duplicate_order_failure_callbacks_ignored"] = int(
                self.diagnostics.get("duplicate_order_failure_callbacks_ignored", 0),
            ) + 1
            return
        seen.add(key)
        current(self, event, event_type)

    setattr(idempotent, "_candidate05_exact_failure_idempotency", True)
    cls._order_failure = idempotent


__all__ = ["install"]
