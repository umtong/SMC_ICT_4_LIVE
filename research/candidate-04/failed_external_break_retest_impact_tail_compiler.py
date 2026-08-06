#!/usr/bin/env python3
"""Require a genuinely high-impact parent break before fading its reclaim failure.

The accepted-break reclaim-failure reversal was profitable only when the parent
break itself was a material price-discovery event. Low-impact breaks that later
lost their reclaim were local auction noise, not trapped-breakout liquidation.

This module changes one relation only: the original break's completed 60-second
absolute return must be at or above the shifted, past-only return quantile already
specified by the candidate configuration. Pool construction, outside acceptance,
first retest, reclaim, reclaim failure, OI routing, stop, target, risk and
NautilusTrader execution remain unchanged.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import failed_external_break_retest_no_impact_cap_ablation_compiler as base


Intent = v22.Intent
LIQUIDATION_SCENARIO = base.LIQUIDATION_SCENARIO
FRESH_SCENARIO = base.FRESH_SCENARIO
# Capture the unrefined parent function before any V27 wrapper temporarily
# replaces the module attribute. Calling base.collect_signals after that
# replacement would recurse into this function.
_ORIGINAL_BASE_COLLECT_SIGNALS = base.collect_signals


def past_only_impact_cutoff(data: pd.DataFrame, config: Any) -> pd.Series:
    """Return a cutoff whose value at t uses observations strictly before t."""

    absolute_return = data["ret_60s_bps"].astype(float).abs()
    return (
        absolute_return.shift(1)
        .rolling(
            int(config.stress_inventory_quantile_window_minutes),
            min_periods=int(config.stress_inventory_quantile_min_periods),
        )
        .quantile(float(config.stress_inventory_quantile))
    )


def is_parent_impact_tail(break_return_bps: float, cutoff_bps: float) -> bool:
    return bool(
        math.isfinite(break_return_bps)
        and math.isfinite(cutoff_bps)
        and abs(break_return_bps) >= cutoff_bps
    )


def _copy_intent(intent: Any, details: dict[str, Any]) -> Intent:
    return Intent(
        scenario=str(intent.scenario),
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
    intents, parent_summary = _ORIGINAL_BASE_COLLECT_SIGNALS(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    cutoff = past_only_impact_cutoff(data, config)
    accepted: list[Intent] = []
    accepted_count = 0
    rejected_count = 0
    for intent in intents:
        break_index = int(intent.details["break_index"])
        break_return = abs(
            float(intent.details["break_state"]["directional_return_60s_bps"])
        )
        threshold = float(cutoff.iloc[break_index])
        passed = is_parent_impact_tail(break_return, threshold)
        details = {
            **intent.details,
            "parent_break_absolute_return_60s_bps": break_return,
            "past_only_parent_break_impact_cutoff_bps": threshold,
            "parent_break_impact_tail": passed,
            "parent_break_impact_quantile": float(
                config.stress_inventory_quantile
            ),
            "compiler": "candidate-04-failed-break-retest-impact-tail",
        }
        if passed:
            accepted.append(_copy_intent(intent, details))
            accepted_count += 1
        else:
            rejected_count += 1

    route_counts = dict(parent_summary.get("route_counts", {}))
    route_counts.update(
        {
            "parent_break_impact_tail_accepted": accepted_count,
            "low_impact_parent_break_rejected": rejected_count,
        }
    )
    return accepted, {
        **parent_summary,
        "candidate": "candidate-04-failed-break-retest-impact-tail",
        "compiler": "candidate-04-failed-break-retest-impact-tail",
        "raw_routed_signals": len(accepted),
        "unique_signal_bars": len(accepted),
        "route_counts": route_counts,
        "structural_refinement": {
            "changed_variables": 1,
            "condition": (
                "original accepted-break absolute 60-second return >= "
                "shifted past-only configured impact quantile"
            ),
            "reason": (
                "a reclaim failure can represent trapped price discovery only "
                "when the parent break was itself a material impact event"
            ),
            "unchanged": [
                "causal external pool",
                "outside acceptance",
                "first exact retest",
                "break-side reclaim",
                "exact reclaim failure",
                "counter-break flow return and basis alignment",
                "state-interval OI routing",
                "causal stop and target",
                "actual-fill guard",
                "current-NAV 3% risk sizing",
                "NautilusTrader execution",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
