#!/usr/bin/env python3
"""Run the generic footprint audit with source-relevant higher timeframes.

The source examples use 5m/15m/1h for intraday execution but also inspect
4h/12h/daily overlapping OBs.  A five-day data warmup cannot fairly conclude
that these structures were absent.  This front end extends footprint history to
thirty days and enumerates 1m, 5m, 15m, 1h, 4h, 12h and daily observations while
leaving the original session-range build/evaluation interval unchanged.
"""
from __future__ import annotations

from datetime import timedelta

import generic_reclaim_footprint_audit as _base


_base.TIMEFRAMES = (1, 5, 15, 60, 240, 720, 1440)
_ORIGINAL_LOAD_RANGE = _base.load_range


def load_range(symbol, start, end, cache):
    return _ORIGINAL_LOAD_RANGE(symbol, start - timedelta(days=25), end, cache)


_base.load_range = load_range


if __name__ == "__main__":
    _base.main()
