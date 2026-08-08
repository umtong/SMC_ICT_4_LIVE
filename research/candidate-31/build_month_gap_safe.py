#!/usr/bin/env python3
"""Run the shared month builder without inventing source premium minutes."""
from __future__ import annotations

from datetime import date

import pandas as pd

import build_month as _base
import build_month_v2  # noqa: F401  # installs explicit-nanosecond as-of joins

_ORIGINAL = _base._exact_minute_grid


def _validate_premium_subset(values: pd.Series, start: date, end: date) -> int:
    actual = pd.DatetimeIndex(pd.to_datetime(values, utc=True).dt.floor("min"))
    expected = pd.date_range(
        pd.Timestamp(start, tz="UTC"),
        pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(minutes=1),
        freq="1min",
    )
    if actual.has_duplicates:
        raise RuntimeError("premium source has duplicate minute observations")
    if not actual.is_monotonic_increasing:
        raise RuntimeError("premium source observations are not monotonic")
    extra = actual.difference(expected)
    if len(extra):
        raise RuntimeError(f"premium source has out-of-range observations: {list(extra[:10])}")
    return int(len(expected.difference(actual)))


def _exact(values: pd.Series, start: date, end: date, label: str) -> None:
    if label == "premium":
        _validate_premium_subset(values, start, end)
        return
    _ORIGINAL(values, start, end, label)


_base._exact_minute_grid = _exact

if __name__ == "__main__":
    _base.main()
