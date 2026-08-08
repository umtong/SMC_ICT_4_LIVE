#!/usr/bin/env python3
"""Pandas-resolution-safe month builder for Candidate 30.

Pandas 3 may preserve exchange epoch resolution as milliseconds for kline bars
and microseconds for metrics.  ``merge_asof`` requires identical dtypes even
when both columns denote the same UTC instants.  This entry point converts only
the two as-of join keys to explicit ``datetime64[ns, UTC]`` before delegating to
the pre-registered builder.  Data values, delays, ownership rules and feature
logic are unchanged.
"""
from __future__ import annotations

import pandas as pd

import build_month as _base

_ORIGINAL_MERGE_ASOF = pd.merge_asof


def _as_utc_ns(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, utc=True, errors="raise")
    return parsed.astype("datetime64[ns, UTC]")


def _merge_asof_ns(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *args: object,
    left_on: str | None = None,
    right_on: str | None = None,
    on: str | None = None,
    **kwargs: object,
) -> pd.DataFrame:
    left_copy = left.copy()
    right_copy = right.copy()
    if on is not None:
        left_copy[on] = _as_utc_ns(left_copy[on])
        right_copy[on] = _as_utc_ns(right_copy[on])
    else:
        if left_on is None or right_on is None:
            raise RuntimeError("candidate30 as-of join requires explicit join keys")
        left_copy[left_on] = _as_utc_ns(left_copy[left_on])
        right_copy[right_on] = _as_utc_ns(right_copy[right_on])
    return _ORIGINAL_MERGE_ASOF(
        left_copy,
        right_copy,
        *args,
        left_on=left_on,
        right_on=right_on,
        on=on,
        **kwargs,
    )


_base.pd.merge_asof = _merge_asof_ns


if __name__ == "__main__":
    _base.main()
