#!/usr/bin/env python3
"""Treat disjoint decision chunks as one continuous fresh account."""
from __future__ import annotations

import numpy as np

import sequential_commitment_router as core

_BASE_PERIOD_NAME = core.period_name
_BASE_ROUTE = core.route


def period_name(directory):
    name = directory.name
    if "fresh-2025-cont" in name:
        return "fresh-2025-continuous"
    return _BASE_PERIOD_NAME(directory)


def route(frame):
    orders, trades, summary = _BASE_ROUTE(frame)
    if frame.period.nunique() == 1 and str(frame.period.iloc[0]).startswith("fresh-2025-continuous"):
        timestamps = frame.order_time_ns.dropna().astype(np.int64)
        if len(timestamps):
            days = max(1, int(np.ceil((timestamps.max() - timestamps.min()) / 86_400_000_000_000)) + 1)
            summary["calendar_days"] = days
            summary["trades_per_day"] = float(len(trades) / days)
    return orders, trades, summary


core.period_name = period_name
core.route = route

if __name__ == "__main__":
    core.main()
