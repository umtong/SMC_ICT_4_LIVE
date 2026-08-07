"""Prevent heterogeneous pandas rows from rounding nanosecond endpoints.

A row selected from a DataFrame containing float market fields can coerce an
int64 timestamp through float64. At 2025 epoch magnitudes, 14.999999999 seconds
can then become 15.000000000 seconds. The five-second path already protects its
bars by storing timestamp values as Python integers. This context applies the
same contract to code paths which reuse the base fifteen-second detector without
changing any market field or state transition.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import run_local_liquidity_sweep_mss_retest as local


@contextmanager
def exact_local_bar_timestamps() -> Iterator[None]:
    original = local._prepare_local_bars

    def prepare(seconds: Any, logic: Any) -> Any:
        bars = original(seconds, logic)
        bars["timestamp_ns"] = bars["timestamp_ns"].map(int).astype(object)
        return bars

    local._prepare_local_bars = prepare
    try:
        yield
    finally:
        local._prepare_local_bars = original


def completed_second_label(timestamp_ns: int) -> int:
    """Return the causal completed wall-clock second for an endpoint.

    Both ``14_999_999_999`` (the final nanosecond of second 14) and
    ``15_000_000_000`` (a float-rounded representation of that endpoint) map to
    completed-second label 15. This helper is only for episode identity; signal
    delivery continues to use the exact timestamp payload.
    """
    value = int(timestamp_ns)
    if value <= 0:
        raise ValueError("timestamp_ns must be positive")
    return (value + 999_999_999) // 1_000_000_000


__all__ = ["completed_second_label", "exact_local_bar_timestamps"]
