#!/usr/bin/env python3
"""Require material new inventory for balanced-session failed breakouts.

The balanced-session failed-inventory route previously accepted every positive
attack-to-reclaim OI change. Cross-development separated a profitable episode
with a substantial exchange OI increase from a losing episode whose tiny change
was below ordinary positive OI snapshot variation. Any non-zero increase is not
sufficient evidence that new breakout inventory became trapped.

This module changes one relation only: attack-to-reclaim OI expansion must be at
or above the median of strictly positive one-step exchange OI changes observed
in the configured rolling window, shifted before the attack. Session balance,
liquidity boundary, attack, delayed reclaim, flow, return, basis, stop, target,
risk and NautilusTrader execution remain unchanged.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import balanced_session_liquidity_reversal_compiler as base


Intent = v22.Intent
FAILED_INVENTORY_SCENARIO = base.FAILED_INVENTORY_SCENARIO
POSITIVE_OI_QUANTILE = 0.50
MIN_POSITIVE_OI_OBSERVATIONS = 30
_ORIGINAL_BASE_COLLECT_SIGNALS = base.collect_signals


def past_only_material_positive_oi_cutoff(
    data: pd.DataFrame,
    config: Any,
) -> pd.Series:
    """Median positive exchange OI step known strictly before each minute."""

    open_interest = data["metric_sum_open_interest"].astype(float)
    change = open_interest.pct_change(fill_method=None)
    positive = change.where(change > 0.0)
    return (
        positive.shift(1)
        .rolling(
            int(config.stress_inventory_quantile_window_minutes),
            min_periods=MIN_POSITIVE_OI_OBSERVATIONS,
        )
        .quantile(POSITIVE_OI_QUANTILE)
    )


def is_material_inventory_expansion(
    state_interval_oi_change: float,
    past_positive_cutoff: float,
) -> bool:
    return bool(
        math.isfinite(state_interval_oi_change)
        and math.isfinite(past_positive_cutoff)
        and past_positive_cutoff > 0.0
        and state_interval_oi_change >= past_positive_cutoff
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
    cutoff = past_only_material_positive_oi_cutoff(data, config)
    accepted: list[Intent] = []
    accepted_count = 0
    rejected_count = 0
    unaffected_count = 0
    for intent in intents:
        if str(intent.scenario) != FAILED_INVENTORY_SCENARIO:
            accepted.append(intent)
            unaffected_count += 1
            continue
        attack_index = int(intent.details["attack_index"])
        interval_change = float(
            intent.details["attack_to_reclaim_open_interest_change"]
        )
        threshold = float(cutoff.iloc[attack_index])
        passed = is_material_inventory_expansion(interval_change, threshold)
        details = {
            **intent.details,
            "past_only_positive_oi_step_median": threshold,
            "positive_oi_step_quantile": POSITIVE_OI_QUANTILE,
            "minimum_positive_oi_observations": MIN_POSITIVE_OI_OBSERVATIONS,
            "material_failed_breakout_inventory": passed,
            "compiler": "candidate-04-balanced-session-material-inventory",
        }
        if passed:
            accepted.append(_copy_intent(intent, details))
            accepted_count += 1
        else:
            rejected_count += 1

    route_counts = dict(parent_summary.get("route_counts", {}))
    route_counts.update(
        {
            "material_failed_inventory_accepted": accepted_count,
            "immaterial_positive_oi_rejected": rejected_count,
            "non_failed_inventory_routes_unchanged": unaffected_count,
        }
    )
    accepted.sort(key=lambda item: int(item.signal_index))
    return accepted, {
        **parent_summary,
        "candidate": "candidate-04-balanced-session-material-inventory",
        "compiler": "candidate-04-balanced-session-material-inventory",
        "raw_routed_signals": len(accepted),
        "unique_signal_bars": len(accepted),
        "route_counts": route_counts,
        "structural_refinement": {
            "changed_variables": 1,
            "old_boundary": "attack-to-reclaim exchange OI change > 0",
            "new_boundary": (
                "state-interval OI expansion >= shifted rolling median of "
                "strictly positive exchange OI snapshot changes"
            ),
            "reason": (
                "failed breakout inventory must be material relative to normal "
                "positive OI updates; a microscopic nonzero change is not a "
                "new leveraged inventory state"
            ),
            "unchanged": [
                "balanced completed-session classification",
                "first next-session high or low consumption",
                "attack-side flow and return",
                "three-bar exact-boundary reclaim",
                "reversal flow return and basis",
                "causal stop and target",
                "actual-fill guard",
                "current-NAV 3% planned-loss sizing",
                "NautilusTrader execution",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
