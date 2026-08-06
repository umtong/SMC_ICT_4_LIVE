#!/usr/bin/env python3
"""V29 auction-state mosaic with extreme impact and material inventory states.

V28 kept every development week profitable but remained below the project growth
objective because two labels were still broader than their economic meaning:

* a marginal q95 return was called an extreme accepted-break impact;
* any positive OI change was called new failed breakout inventory.

V29 applies both independently testable corrections while retaining V28's
upper-tail directional-session boundary:

1. accepted-break reclaim-failure liquidation requires a shifted past-only q99
   parent break impact;
2. balanced-session failed breakout requires attack-to-reclaim OI expansion at
   or above the shifted rolling median of positive exchange OI steps.

All other state order, liquidity definitions, stops, targets, risk sizing and
NautilusTrader execution are unchanged.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v28 as v28
import failed_external_break_retest_extreme_tail_compiler as extreme
import balanced_session_material_inventory_compiler as material


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    original_impact = v28.impact_tail.collect_signals
    original_balanced = v28.v27.balanced_session.collect_signals
    v28.impact_tail.collect_signals = extreme.collect_signals
    v28.v27.balanced_session.collect_signals = material.collect_signals
    try:
        intents, summary = v28.collect_signals(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
            router,
        )
    finally:
        v28.impact_tail.collect_signals = original_impact
        v28.v27.balanced_session.collect_signals = original_balanced

    router_contract = dict(summary.get("router_contract", {}))
    router_contract["v29_state_boundaries"] = {
        "failed_break_liquidation": (
            "original accepted break absolute 60-second return >= shifted "
            "past-only q99"
        ),
        "balanced_failed_inventory": (
            "attack-to-reclaim OI expansion >= shifted rolling median of "
            "strictly positive exchange OI steps"
        ),
    }
    return intents, {
        **summary,
        "candidate": "candidate-04-v29-auction-state-mosaic",
        "compiler": "candidate-04-v29",
        "router_contract": router_contract,
        "changes_from_v28": {
            "changed_state_boundaries": 2,
            "independently_tested_boundaries": [
                "parent-break impact q95 to q99",
                "balanced-session OI > 0 to material positive-step median",
            ],
            "unchanged": [
                "all causal liquidity detectors",
                "all state ordering and confirmation windows",
                "directional-session upper-tail classification",
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
