"""Compatibility repair for pandas datetime-unit changes in funding evidence."""
from __future__ import annotations

import pandas as pd

import funding_evidence_v2 as _funding


def _as_ns(values: pd.Series, name: str) -> pd.Series:
    """Return integer nanoseconds regardless of pandas' internal datetime unit.

    Pandas may store parsed timestamps as ``datetime64[us, UTC]``. Casting that
    array to ``int64`` then returns microseconds, while Nautilus event times are
    nanoseconds. ``Timestamp.value`` is explicitly nanoseconds and therefore
    preserves the time-unit contract across pandas versions.
    """
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    if parsed.isna().any():
        bad = values[parsed.isna()].astype(str).tolist()
        raise RuntimeError(f"invalid {name} timestamps in trade audit: {bad}")
    return parsed.map(lambda item: int(item.value)).astype("int64")


# Preserve one implementation of the funding ledger join while repairing the
# environment-specific timestamp conversion before either tests or the runner
# call it. This can be folded into the base module after the current diagnostic.
_funding._as_ns = _as_ns
