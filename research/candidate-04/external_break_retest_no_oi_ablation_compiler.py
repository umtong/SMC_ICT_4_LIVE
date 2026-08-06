#!/usr/bin/env python3
"""Ablate only retest OI contraction from external-break retest resumption.

Every causal pool, break, outside acceptance, flow/return/basis relation, exact
first retest, reclaim, stop, target, actual-fill guard, risk and NautilusTrader
execution rule remains unchanged. Original OI values are restored in emitted
signal details for diagnosis; they no longer gate the retest.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import external_break_retest_compiler as base


SCENARIO = "ACCEPTED_EXTERNAL_POOL_BREAK_RETEST_NO_OI_ABLATION"


def _copy_intent(intent: Any, original_oi: float) -> Any:
    details = dict(intent.details)
    counter = dict(details["retest_counter_break_state"])
    counter["open_interest_change_15m"] = original_oi
    counter["open_interest_contraction_gate_required"] = False
    details["retest_counter_break_state"] = counter
    details["ablation"] = "removed negative 15-minute OI-change requirement only"
    details["compiler"] = "candidate-04-external-break-retest-no-oi-ablation"
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
    ablated["oi_change_xday_15m"] = -1.0
    intents, counts = base.detect_external_break_retest_intents(
        ablated,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    restored = [
        _copy_intent(
            intent,
            float(original_oi.iloc[int(intent.details["retest_index"])]),
        )
        for intent in intents
    ]
    return restored, {
        "candidate": "candidate-04-external-break-retest-no-oi-ablation",
        "compiler": "candidate-04-external-break-retest-no-oi-ablation",
        "raw_routed_signals": len(restored),
        "unique_signal_bars": len(restored),
        "route_counts": counts,
        "ablation": {
            "removed_variable": "negative 15-minute open-interest change at first retest",
            "changed_variables": 1,
            "unchanged": [
                "first accepted break of an aged/prominent causal pivot pool",
                "outside close with aligned executed flow, return and basis",
                "non-climactic outside acceptance",
                "first meaningful retest of the exact broken pool",
                "counter-break executed flow, return and basis",
                "exact pool reclaim within three bars",
                "break-side flow, return and basis on reclaim",
                "reclaim displacement no larger than retest shock",
                "complete retest/reclaim invalidation",
                "causal external-liquidity target",
                "actual-fill guard and 3% NAV risk contract",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
