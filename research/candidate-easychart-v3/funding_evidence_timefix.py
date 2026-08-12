"""Compatibility repair for pandas datetime-unit changes in funding evidence."""
from __future__ import annotations

import pandas as pd

import funding_evidence_v2 as _funding


def _as_ns(values: pd.Series, name: str) -> pd.Series:
    """Return integer nanoseconds with the trade audit's exact row index.

    Pandas may store parsed timestamps as ``datetime64[us, UTC]``. Casting that
    array to ``int64`` then returns microseconds, while Nautilus event times are
    nanoseconds. Constructing an explicit Series from ``Timestamp.value`` both
    fixes the unit and prevents pandas from substituting a datetime dtype or a
    different index during ``map`` inference.
    """
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    if parsed.isna().any():
        bad = values[parsed.isna()].astype(str).tolist()
        raise RuntimeError(f"invalid {name} timestamps in trade audit: {bad}")
    return pd.Series(
        [int(pd.Timestamp(item).value) for item in parsed.tolist()],
        index=values.index,
        dtype="int64",
    )


_funding._as_ns = _as_ns
