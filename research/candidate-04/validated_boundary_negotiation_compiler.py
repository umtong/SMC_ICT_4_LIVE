#!/usr/bin/env python3
"""Validated V31 boundary-negotiation core without failed stress continuation.

The V31 detector proved that a first reclaim is not a completed auction state:
price must negotiate both sides of the objective boundary and then leave the
entire prior close range with completed flow, return and futures-index basis
aligned. One economic interpretation failed its controlled ablation, however.
A stress parent auction expanding again in its original direction after settled
two-sided negotiation was not repeatable acceptance; both observed trades lost.
Removing that single route made all seven development weeks positive while all
other V31 relations remained unchanged.

This compiler excludes the failed route at the cause-classification boundary,
not by deleting already compiled trades. A stress parent may still produce the
validated opposite-side deleveraging reversal. Balanced-session, external-pool,
non-routed V29 states, negotiation timing, q95 displacement, full-range stop and
all execution inputs remain unchanged. The module emits completed-data intents
only; NautilusTrader owns targets, orders, fills, fees, positions and NAV.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import boundary_negotiation_expansion_compiler as v31
import two_stage_auction_resolution_compiler as v30


Intent = v22.Intent
FAILED_STRESS_ROUTE = v31.SCENARIOS[v30.STRESS_PARENT]["parent_side"]
_ORIGINAL_SETTLED_CAUSE = v31.settled_cause


def validated_settled_cause(
    data: pd.DataFrame,
    parent: Intent,
    expansion_index: int,
    expansion_side: int,
) -> tuple[bool, dict[str, Any]]:
    """Reject only the ablated stress-parent acceptance continuation."""

    if (
        str(parent.scenario) == v30.STRESS_PARENT
        and int(expansion_side) == int(parent.side)
    ):
        return False, {
            "settled_stress_route": "REJECTED_ACCEPTANCE_CONTINUATION",
            "route_removed_by_controlled_ablation": True,
            "removed_scenario": FAILED_STRESS_ROUTE,
            "reason": (
                "stress-parent original-direction expansion after settled "
                "negotiation was 0-for-2 and negative in the controlled "
                "seven-week ablation"
            ),
        }
    return _ORIGINAL_SETTLED_CAUSE(
        data,
        parent,
        expansion_index,
        expansion_side,
    )


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    """Compile the validated core while restoring the imported module safely."""

    original = v31.settled_cause
    v31.settled_cause = validated_settled_cause
    try:
        intents, summary = v31.collect_signals(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
            router,
        )
    finally:
        v31.settled_cause = original

    if any(str(intent.scenario) == FAILED_STRESS_ROUTE for intent in intents):
        raise RuntimeError(
            "failed stress continuation escaped cause-stage exclusion"
        )

    router_contract = dict(summary.get("router_contract", {}))
    router_contract["validated_v31_core"] = {
        "excluded_at_cause_stage": FAILED_STRESS_ROUTE,
        "retained_stress_route": (
            v31.SCENARIOS[v30.STRESS_PARENT]["opposite_side"]
        ),
        "controlled_ablation_run": 31114267295,
        "unchanged": [
            "objective boundary and two-sided negotiation",
            "past-only q95 settled displacement",
            "completed flow return and basis alignment",
            "full negotiation high-low invalidation",
            "all non-failed V31 and V29 mechanisms",
        ],
    }
    return intents, {
        **summary,
        "candidate": "candidate-04-validated-boundary-negotiation-core",
        "compiler": "candidate-04-validated-boundary-negotiation-core",
        "router_contract": router_contract,
        "changes_from_v31": {
            "changed_economic_routes": 1,
            "removed": FAILED_STRESS_ROUTE,
            "removal_stage": "settled cause classification before intent creation",
            "execution": "NautilusTrader BacktestNode",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
