#!/usr/bin/env python3
"""V26c: correct the OI semantics of orderly inventory displacement.

The frozen V26b first untouched week exposed an implementation-meaning error in
``ORDERLY_INVENTORY_DISPLACEMENT``. Its inherited detector stored
``side * raw_oi_change_15m`` and therefore treated falling OI as positive for a
short. Falling OI during a price decline can be long liquidation; it is not
fresh short inventory creation.

V26c changes only that meaning for this one scenario:

    raw exchange open-interest change over 15 completed minutes > 0

for both long and short entries. Every price, flow, basis, acceptance, stop,
target, risk and execution condition remains unchanged. This module compiles
completed-data intents only. NautilusTrader remains sole owner of orders,
actual fills, fees, positions, PnL, margin, liquidation and NAV.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v26b as v26b


Intent = v26b.Intent
SCENARIO = "ORDERLY_INVENTORY_DISPLACEMENT"


def raw_open_interest_confirms_creation(details: dict[str, Any]) -> bool:
    value = float(details.get("raw_oi_change_15m", float("nan")))
    return math.isfinite(value) and value > 0.0


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    parents, summary = v26b.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    accepted: list[Intent] = []
    rejected = 0
    for parent in parents:
        if parent.scenario != SCENARIO:
            accepted.append(parent)
            continue
        if not raw_open_interest_confirms_creation(parent.details):
            rejected += 1
            continue
        details = {
            **parent.details,
            "inventory_oi_semantics": (
                "raw OI change must be positive for both long and short"
            ),
            "implementation_fix_from_v26b": (
                "short-side OI contraction is no longer sign-flipped into "
                "fresh inventory creation"
            ),
            "compiler": "candidate-04-v26c",
        }
        accepted.append(
            Intent(
                scenario=parent.scenario,
                side=int(parent.side),
                signal_index=int(parent.signal_index),
                entry_index=int(parent.entry_index),
                stop_level=float(parent.stop_level),
                event_indices=tuple(int(value) for value in parent.event_indices),
                details=details,
            )
        )

    route_counts = dict(summary.get("route_counts", {}))
    route_counts["orderly_inventory_raw_oi_contraction_rejected"] = rejected
    route_counts["orderly_inventory_after_oi_semantic_fix"] = sum(
        intent.scenario == SCENARIO for intent in accepted
    )
    return accepted, {
        **summary,
        "candidate": "candidate-04-v26c-corrected-inventory-semantics",
        "compiler": "candidate-04-v26c",
        "raw_routed_signals": len(accepted),
        "unique_signal_bars": len(accepted),
        "route_counts": route_counts,
        "implementation_fix": {
            "scenario": SCENARIO,
            "old_meaning": "side-adjusted OI change >= 0",
            "new_meaning": "raw exchange OI change > 0",
            "changed_variables": 1,
            "unchanged": [
                "market prices",
                "executed flow",
                "five-minute acceptance",
                "auction path",
                "basis central band",
                "causal stop",
                "causal target",
                "risk sizing",
                "NautilusTrader execution"
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
