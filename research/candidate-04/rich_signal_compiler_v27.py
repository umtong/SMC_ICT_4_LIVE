#!/usr/bin/env python3
"""V27: integrate orthogonal completed auction states under one portfolio.

V27 does not relax thresholds or merge distinct causes with an OR. It preserves
four independently diagnosed market mechanisms and removes branches rejected by
controlled same-week tests:

1. V26c/V25 failed-auction states, excluding rejected
   ORDERLY_INVENTORY_DISPLACEMENT;
2. accepted external-break retest reclaim failure after state-interval OI
   contraction, excluding the fresh-counterinventory route which lost;
3. first balanced-session boundary sweep with OI expansion, excluding the
   liquidation route which lost; and
4. directional-session VWAP pullback/reclaim with OI contraction, whose two
   observed trades both won.

Each compiler emits completed-data intents only. This router resolves only
same-bar semantic conflicts; it never simulates fills or positions. One global
NautilusTrader strategy remains sole owner of orders, actual fills, fees,
positions, risk, PnL, margin, liquidation and NAV.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v26c as v26c
import failed_external_break_retest_no_impact_cap_ablation_compiler as failed_break
import balanced_session_liquidity_reversal_compiler as balanced_session
import directional_session_vwap_reclaim_compiler as directional_session


Intent = v22.Intent
REJECTED_SCENARIOS = {
    "ORDERLY_INVENTORY_DISPLACEMENT",
    failed_break.FRESH_SCENARIO,
    balanced_session.LIQUIDATION_SCENARIO,
    directional_session.TRAPPED_COUNTER_SCENARIO,
}
ADMITTED_COMPLEMENTARY_SCENARIOS = {
    failed_break.LIQUIDATION_SCENARIO,
    balanced_session.FAILED_INVENTORY_SCENARIO,
    directional_session.LIQUIDATION_SCENARIO,
}


def admitted_v26c(intent: Intent) -> bool:
    return str(intent.scenario) != "ORDERLY_INVENTORY_DISPLACEMENT"


def _priority(scenario: str) -> int:
    # Longer, more state-specific chains win only when two completed decisions
    # occur on the exact same bar. Portfolio overlap remains a Nautilus concern.
    order = {
        failed_break.LIQUIDATION_SCENARIO: 0,
        balanced_session.FAILED_INVENTORY_SCENARIO: 1,
        directional_session.LIQUIDATION_SCENARIO: 2,
        "EXTERNAL_POOL_FAILED_DISCOVERY_REVERSAL": 3,
        "TRAPPED_COUNTERTREND_INVENTORY_RETEST_RESUMPTION": 4,
        "NON_CLIMACTIC_PARENT_AUCTION_RESUMPTION": 5,
        "NORMAL_FAILED_AUCTION_RESUMPTION": 6,
        "TAIL_CONFIRMED_STRESS_FAILED_AUCTION": 7,
    }
    return order.get(scenario, 99)


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    base_intents, base_summary = v26c.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    failed_intents, failed_summary = failed_break.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    balanced_intents, balanced_summary = balanced_session.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    directional_intents, directional_summary = directional_session.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )

    selected: list[Intent] = [
        intent for intent in base_intents if admitted_v26c(intent)
    ]
    selected.extend(
        intent
        for intent in failed_intents
        if str(intent.scenario) == failed_break.LIQUIDATION_SCENARIO
    )
    selected.extend(
        intent
        for intent in balanced_intents
        if str(intent.scenario) == balanced_session.FAILED_INVENTORY_SCENARIO
    )
    selected.extend(
        intent
        for intent in directional_intents
        if str(intent.scenario) == directional_session.LIQUIDATION_SCENARIO
    )

    selected.sort(
        key=lambda item: (
            int(item.signal_index),
            _priority(str(item.scenario)),
        )
    )
    unique: list[Intent] = []
    seen: set[int] = set()
    same_bar_conflicts = 0
    scenario_counts: dict[str, int] = {}
    for intent in selected:
        index = int(intent.signal_index)
        if index in seen:
            same_bar_conflicts += 1
            continue
        seen.add(index)
        unique.append(intent)
        scenario = str(intent.scenario)
        scenario_counts[scenario] = scenario_counts.get(scenario, 0) + 1

    base_rejected = sum(
        str(intent.scenario) == "ORDERLY_INVENTORY_DISPLACEMENT"
        for intent in base_intents
    )
    rejected_branch_counts = {
        "orderly_inventory": base_rejected,
        "fresh_inventory_failed_break_reversal": sum(
            str(intent.scenario) == failed_break.FRESH_SCENARIO
            for intent in failed_intents
        ),
        "balanced_session_liquidation_reversal": sum(
            str(intent.scenario) == balanced_session.LIQUIDATION_SCENARIO
            for intent in balanced_intents
        ),
        "directional_session_trapped_counterinventory": sum(
            str(intent.scenario) == directional_session.TRAPPED_COUNTER_SCENARIO
            for intent in directional_intents
        ),
    }
    return unique, {
        "candidate": "candidate-04-v27-auction-state-mosaic",
        "compiler": "candidate-04-v27",
        "raw_routed_signals": len(selected),
        "unique_signal_bars": len(unique),
        "route_counts": {
            "admitted_by_scenario": scenario_counts,
            "same_bar_semantic_conflicts": same_bar_conflicts,
            "rejected_branch_counts": rejected_branch_counts,
            "v26c": base_summary.get("route_counts"),
            "failed_break": failed_summary.get("route_counts"),
            "balanced_session": balanced_summary.get("route_counts"),
            "directional_session": directional_summary.get("route_counts"),
        },
        "router_contract": {
            "global_position_constraint": (
                "all scenarios share one NautilusTrader strategy and portfolio"
            ),
            "admitted_mechanisms": [
                "V25/V26c failed-auction states except orderly inventory displacement",
                "accepted-break retest failure after OI contraction",
                "balanced-session boundary reversal after OI expansion",
                "directional-session VWAP reclaim after OI contraction",
            ],
            "rejected_mechanisms": sorted(REJECTED_SCENARIOS),
            "same_bar_resolution": (
                "longer causal chain first; no pre-simulation of position overlap"
            ),
            "target_and_execution": "causal external liquidity through NautilusTrader",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
