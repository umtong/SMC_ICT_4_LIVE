#!/usr/bin/env python3
"""Ablate only the OI-contraction gate from parent internal resumption.

All parent-auction, causal pivot-pool, discount/premium, attack-flow, reclaim,
relative-strength, stop, target, risk and NautilusTrader execution contracts are
unchanged. The original observed OI value is restored in each emitted intent so
the evidence remains diagnosable; it simply does not gate the signal.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import parent_internal_liquidation_resumption_compiler as base


SCENARIO = "PARENT_ALIGNED_INTERNAL_POOL_RESUMPTION_NO_OI_ABLATION"


def _copy_intent(intent: Any, original_oi: float) -> Any:
    details = {
        **intent.details,
        "shock_open_interest_change_15m": original_oi,
        "open_interest_contraction_gate_required": False,
        "ablation": "removed negative 15-minute OI-change requirement only",
        "compiler": "candidate-04-parent-internal-no-oi-ablation",
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
    original_oi = data["oi_change_xday_15m"].astype(float).copy()
    ablated = data.copy()
    # A finite negative sentinel passes exactly the one removed gate. No other
    # feature or timestamp is changed.
    ablated["oi_change_xday_15m"] = -1.0
    intents, counts = base.detect_parent_internal_liquidation_intents(
        ablated,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    restored = [
        _copy_intent(
            intent,
            float(original_oi.iloc[int(intent.details["shock_index"])]),
        )
        for intent in intents
    ]
    return restored, {
        "candidate": "candidate-04-parent-internal-no-oi-ablation",
        "compiler": "candidate-04-parent-internal-no-oi-ablation",
        "raw_routed_signals": len(restored),
        "unique_signal_bars": len(restored),
        "route_counts": counts,
        "ablation": {
            "removed_variable": "negative 15-minute open-interest change gate",
            "changed_variables": 1,
            "unchanged": [
                "completed pre-shock 480-minute parent auction",
                "parent displacement greater than shock magnitude",
                "first causal pivot-pool penetration",
                "parent discount/premium internal-pool location",
                "counter-parent attack flow and return",
                "exact pool reclaim within three completed bars",
                "parent-side reclaim flow and return",
                "confirmation displacement no larger than shock",
                "complete shock/reclaim invalidation",
                "causal external-liquidity target",
                "actual-fill guard and 3% NAV risk contract",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
