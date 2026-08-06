#!/usr/bin/env python3
"""Configuration-safe entrypoint for exact causal-pool reclaim reversal.

V1 accidentally deleted the local pool configuration before detector use. V2
corrected the detector but used a lowercase JSON boolean in the summary builder.
This wrapper changes neither market logic nor detector output; it provides the
valid Python entrypoint used for the controlled two-week screen.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import causal_pool_reclaim_reversal_compiler_v2 as base
import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401


Intent = base.Intent
CONFIRMATION_BARS = base.CONFIRMATION_BARS


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    del router
    intents, counts = base.detect_causal_pool_reclaim_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-causal-pool-reclaim-reversal-v3",
        "compiler": "candidate-04-causal-pool-reclaim-v3",
        "implementation_fixes": [
            "preserve supplied config for causal pool detection",
            "use a valid Python boolean in the evidence summary",
        ],
        "scenario_logic_changed_from_v1": False,
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "liquidity": (
                "first meaningful penetration of an aged/prominent causal "
                "right-confirmed pivot pool"
            ),
            "attack": "executed flow and return aligned through the pool",
            "reclaim": "later close back inside the exact pool within three bars",
            "turn": (
                "reversal-side executed flow, return and basis change with "
                "reversal return no larger than the attack"
            ),
            "excluded": "same-bar displayed-depth confirmation",
            "invalidation": "complete attack/reclaim extreme plus ATR buffer",
            "target_and_execution": "causal external liquidity through NautilusTrader",
        },
        "constants": {
            "confirmation_bars": CONFIRMATION_BARS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
