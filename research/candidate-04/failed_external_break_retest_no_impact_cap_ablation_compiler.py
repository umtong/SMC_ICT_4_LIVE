#!/usr/bin/env python3
"""Ablate only the reclaim-failure displacement ceiling.

The base failed-break-retest reversal requires failure return to be no larger
than the original retest shock. This controlled ablation asks whether a stronger
close back through the exact pool is instead evidence of trapped-breakout
liquidation. All parent, pool, acceptance, retest, reclaim, OI routing, flow,
basis, stop, target, risk and NautilusTrader execution rules are unchanged.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import failed_external_break_retest_reversal_compiler as base


LIQUIDATION_SCENARIO = (
    "FAILED_EXTERNAL_BREAK_RETEST_LIQUIDATION_REVERSAL_NO_IMPACT_CAP_ABLATION"
)
FRESH_SCENARIO = (
    "FAILED_EXTERNAL_BREAK_RETEST_FRESH_COUNTER_INVENTORY_REVERSAL_NO_IMPACT_CAP_ABLATION"
)


def positive_failure_return(
    failure_return_bps: float,
    retest_return_bps: float,
) -> bool:
    del retest_return_bps
    return math.isfinite(failure_return_bps) and failure_return_bps > 0.0


def _copy_intent(intent: Any) -> Any:
    liquidation = intent.scenario == base.LIQUIDATION_SCENARIO
    details = {
        **intent.details,
        "ablation": (
            "removed failure_return <= original_retest_return ceiling only"
        ),
        "failure_displacement_cap_required": False,
        "compiler": "candidate-04-failed-break-retest-no-impact-cap-ablation",
    }
    return base.Intent(
        scenario=LIQUIDATION_SCENARIO if liquidation else FRESH_SCENARIO,
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
    original = base.non_impact_failure
    base.non_impact_failure = positive_failure_return
    try:
        intents, counts = base.detect_failed_break_retest_reversals(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
        )
    finally:
        base.non_impact_failure = original
    copied = [_copy_intent(intent) for intent in intents]
    return copied, {
        "candidate": "candidate-04-failed-break-retest-no-impact-cap-ablation",
        "compiler": "candidate-04-failed-break-retest-no-impact-cap-ablation",
        "raw_routed_signals": len(copied),
        "unique_signal_bars": len(copied),
        "route_counts": counts,
        "ablation": {
            "removed_variable": (
                "failure displacement must be no larger than retest shock"
            ),
            "changed_variables": 1,
            "unchanged": [
                "accepted causal external-pool break",
                "non-climactic outside acceptance",
                "first exact-pool retest",
                "break-side reclaim",
                "exact pool loss within five completed bars",
                "counter-break flow return and five-minute basis alignment",
                "acceptance-to-retest OI inventory routing",
                "complete reclaim-failure stop",
                "causal external-liquidity target",
                "actual-fill geometry guard",
                "current-NAV 3% risk sizing",
                "NautilusTrader execution",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
