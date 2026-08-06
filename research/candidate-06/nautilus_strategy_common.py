"""Small framework-neutral conversion helpers for candidate-06 execution records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def money_to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "as_decimal"):
        return float(value.as_decimal())
    return float(value)


def utc_hour(ts_ns: int) -> int:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).hour


def utc_day(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()
