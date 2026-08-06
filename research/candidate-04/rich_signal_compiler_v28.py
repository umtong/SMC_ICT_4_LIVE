#!/usr/bin/env python3
"""V28 auction-state mosaic with two independent state-boundary corrections.

V27 kept six non-overlapping development weeks profitable, but two mechanisms
mixed causally distinct states:

* reclaim failure after a low-impact local break was treated like trapped
  price-discovery liquidation;
* a marginally above-median 8-hour session was treated like directional value
  migration.

V28 applies both independently validated, past-only boundaries:

1. failed-break liquidation reversal requires the original accepted break to be
   in the configured shifted past-only 60-second return tail;
2. directional-session VWAP reclaim requires parent-session efficiency above
   the shifted prior completed-session upper quartile.

All V27 pool construction, state ordering, targets, stops, causal timestamps,
risk sizing and NautilusTrader execution remain unchanged.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v27 as v27
import failed_external_break_retest_impact_tail_compiler as impact_tail
import directional_session_vwap_upper_tail_compiler as directional_upper


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    original_failed = v27.failed_break.collect_signals
    original_directional = v27.directional_session.collect_signals
    v27.failed_break.collect_signals = impact_tail.collect_signals
    v27.directional_session.collect_signals = directional_upper.collect_signals
    try:
        intents, summary = v27.collect_signals(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
            router,
        )
    finally:
        v27.failed_break.collect_signals = original_failed
        v27.directional_session.collect_signals = original_directional

    router_contract = dict(summary.get("router_contract", {}))
    router_contract["v28_state_boundaries"] = {
        "failed_break_liquidation": (
            "original accepted break is in shifted past-only configured "
            "60-second absolute-return tail"
        ),
        "directional_session": (
            "completed parent-session efficiency is above shifted prior "
            "completed-session 75th percentile and its close remains beyond "
            "one realized VWAP MAD"
        ),
    }
    return intents, {
        **summary,
        "candidate": "candidate-04-v28-auction-state-mosaic",
        "compiler": "candidate-04-v28",
        "router_contract": router_contract,
        "changes_from_v27": {
            "changed_state_boundaries": 2,
            "independent_boundaries": [
                "parent-break impact tail",
                "parent-session efficiency upper quartile",
            ],
            "unchanged": [
                "all liquidity-pool detectors",
                "all event ordering and confirmation windows",
                "all OI inventory routes",
                "all causal stops and external-liquidity targets",
                "actual-fill geometry guard",
                "current-NAV 3% planned-loss sizing",
                "single global pending-entry/open-position constraint",
                "NautilusTrader orders fills fees positions and NAV",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
