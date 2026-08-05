#!/usr/bin/env python3
"""Controlled 14-bps event-clock run for resolved impact.

This wrapper changes exactly one variable in ``impact_resolution_adaptive_week``:
the preceding-day median event-range floor is the 14 bps round-trip execution
cost itself rather than the stricter 26 bps price-risk-share identity.  The
scenario and every trading/execution rule remain unchanged.  It exists only to
separate event-clock opportunity loss from scenario logic on the first BTC
week.
"""

from __future__ import annotations

import impact_resolution_adaptive_week as candidate


candidate.MINIMUM_EVENT_RANGE_BPS = candidate.ROUND_TRIP_COST_BPS


if __name__ == "__main__":
    raise SystemExit(candidate.run(candidate.build_parser().parse_args()))
