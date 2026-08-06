#!/usr/bin/env python3
"""Ablate only the five-minute basis-turn gate from pool reclaim reversal.

Causal pivot observation, first penetration, attack flow/return, exact later
reclaim, reversal flow/return, relative displacement, stop, target, actual-fill
guard, risk and NautilusTrader execution remain unchanged. Original basis values
remain in signal details; they no longer gate confirmation.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import causal_pool_reclaim_reversal_compiler_v2 as base
import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401


SCENARIO = "EXACT_CAUSAL_POOL_RECLAIM_NO_BASIS_ABLATION"


def reversal_without_basis(state: Any, attack_return_bps: float) -> bool:
    return bool(
        state is not None
        and state.flow > 0.0
        and state.return_bps > 0.0
        and math.isfinite(attack_return_bps)
        and 0.0 < state.return_bps <= attack_return_bps
    )


def _copy_intent(intent: Any) -> Any:
    details = {
        **intent.details,
        "basis_turn_gate_required": False,
        "ablation": "removed positive reversal-direction 5-minute basis change only",
        "compiler": "candidate-04-causal-pool-reclaim-no-basis-ablation",
    }
    return base.Intent(
        scenario=SCENARIO,
        side=int(intent.side),
        signal_index=int(intent.signal_index),
        entry_index=int(intent.entry_index),
        stop_level=float(intent.stop_level),
        event_indices=tuple(int(value) for value in intent.event_indices),
        details=details,
    )


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    del router
    original = base.base.reversal_confirmed
    base.base.reversal_confirmed = reversal_without_basis
    try:
        intents, counts = base.detect_causal_pool_reclaim_intents(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
        )
    finally:
        base.base.reversal_confirmed = original
    restored = [_copy_intent(intent) for intent in intents]
    return restored, {
        "candidate": "candidate-04-causal-pool-reclaim-no-basis-ablation",
        "compiler": "candidate-04-causal-pool-reclaim-no-basis-ablation",
        "raw_routed_signals": len(restored),
        "unique_signal_bars": len(restored),
        "route_counts": counts,
        "ablation": {
            "removed_variable": "positive reversal-direction five-minute basis change",
            "changed_variables": 1,
            "unchanged": [
                "aged/prominent right-confirmed causal pivot pool",
                "first meaningful penetration consumption",
                "attack-side executed flow and return",
                "later close back inside the exact pool within three bars",
                "reversal-side executed flow and return",
                "reversal displacement no larger than attack displacement",
                "same-bar depth confirmation excluded",
                "complete attack/reclaim invalidation",
                "causal external-liquidity target",
                "actual-fill guard and 3% NAV risk contract",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
