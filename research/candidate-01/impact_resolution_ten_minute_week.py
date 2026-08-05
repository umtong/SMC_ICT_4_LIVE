#!/usr/bin/env python3
"""Resolved impact on a causal daily ten-minute-equivalent information clock.

The only changed variable from ``impact_resolution_adaptive_week`` is clock
selection.  Each UTC day freezes the equal-notional target implied by the
immediately preceding completed day's median ten-minute quote activity.

Ten minutes is declared from the holding contract rather than PnL: the shared
45-event maximum hold corresponds to roughly 7.5 hours, preserving an intraday
scenario horizon.  No clock candidate is selected from backtest performance.
"""

from __future__ import annotations

import impact_resolution_adaptive_week as candidate


candidate.DEFAULT_CANDIDATE_MINUTES = (10,)
candidate.MINIMUM_EVENT_RANGE_BPS = 1e-6


if __name__ == "__main__":
    raise SystemExit(candidate.run(candidate.build_parser().parse_args()))
