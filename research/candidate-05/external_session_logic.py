"""Pure causal clock logic for completed activity-session liquidity."""
from __future__ import annotations

from datetime import datetime, timezone


def validate_uniform_session_hours(session_hours: tuple[int, ...]) -> int:
    """Return uniform UTC session span, including the midnight wrap."""
    if not session_hours:
        raise ValueError("session_hours must not be empty")
    if tuple(sorted(set(session_hours))) != session_hours:
        raise ValueError("session_hours must be sorted and unique")
    if session_hours[0] != 0 or any(hour < 0 or hour > 23 for hour in session_hours):
        raise ValueError("session_hours must start at UTC hour zero and stay in [0, 23]")
    gaps = [right - left for left, right in zip(session_hours, session_hours[1:])]
    gaps.append(24 - session_hours[-1])
    if len(set(gaps)) != 1:
        raise ValueError("session_hours must define a uniform completed auction clock")
    return gaps[0]


def utc_session_key(ts_event_ns: int, session_hours: tuple[int, ...]) -> int:
    """Causal key for the UTC activity session containing ``ts_event_ns``."""
    validate_uniform_session_hours(session_hours)
    moment = datetime.fromtimestamp(ts_event_ns / 1_000_000_000, tz=timezone.utc)
    boundary = max(hour for hour in session_hours if hour <= moment.hour)
    return int(moment.strftime("%Y%m%d")) * 100 + boundary


__all__ = ["utc_session_key", "validate_uniform_session_hours"]
