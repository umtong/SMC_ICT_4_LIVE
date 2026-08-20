#!/usr/bin/env python3
"""Continuous-account executable for economic pending-order replacement."""
from __future__ import annotations

import numpy as np

import sequential_commitment_router_replacement as replacement

economic = replacement.economic
_ORIGINAL_PERIOD_NAME = economic.base.period_name
_ORIGINAL_ROUTE = replacement.route


def period_name(directory):
    if "fresh-2025-cont" in directory.name:
        return "fresh-2025-continuous"
    return _ORIGINAL_PERIOD_NAME(directory)


def route(frame):
    orders, trades, summary = _ORIGINAL_ROUTE(frame)
    if frame.period.nunique() == 1 and str(frame.period.iloc[0]) == "fresh-2025-continuous":
        timestamps = frame.order_time_ns.dropna().astype(np.int64)
        if len(timestamps):
            days = max(
                1,
                int(np.ceil((timestamps.max() - timestamps.min()) / 86_400_000_000_000)) + 1,
            )
            summary["calendar_days"] = days
            summary["trades_per_day"] = float(len(trades) / days)
    return orders, trades, summary


economic.base.period_name = period_name
replacement.route = route
economic.route = route

if __name__ == "__main__":
    economic.main()
