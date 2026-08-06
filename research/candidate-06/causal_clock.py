"""Clock helpers which map a completed one-minute bar to its source interval."""

from __future__ import annotations

from datetime import datetime, timezone


ONE_MINUTE_NS = 60_000_000_000


def source_bar_datetime(ts_ns: int) -> datetime:
    """Return UTC time of the source interval, not the completed-bar event time."""
    return datetime.fromtimestamp((int(ts_ns) - ONE_MINUTE_NS) / 1_000_000_000, tz=timezone.utc)


class CompletedBarClockMixin:
    @staticmethod
    def _datetime(ts_ns: int) -> datetime:
        return source_bar_datetime(ts_ns)
