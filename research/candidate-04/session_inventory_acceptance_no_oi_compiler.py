#!/usr/bin/env python3
"""Session inventory-acceptance ablation without the OI-creation gate.

This wrapper changes exactly one logical variable from
``session_inventory_acceptance_compiler.py``: positive 15-minute open-interest
change is observed and recorded but no longer required. Executed flow, price
return, directional basis expansion, outside-range persistence, non-climactic
confirmation, invalidation, stop, target, risk and NautilusTrader execution are
unchanged.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import session_inventory_acceptance_compiler as base


def alignment_without_oi(row: pd.Series, side: int) -> tuple[bool, dict[str, float]]:
    flow = side * float(row["flow_60s"])
    directional_return = side * float(row["ret_60s_bps"])
    oi_change = float(row["metric_oi_change_15m"])
    basis_change = side * float(row["basis_change_5m"])
    finite_required = all(
        math.isfinite(value)
        for value in (flow, directional_return, basis_change)
    )
    passed = (
        finite_required
        and flow > 0.0
        and directional_return > 0.0
        and basis_change > 0.0
    )
    return passed, {
        "directional_flow_60s": flow,
        "directional_return_60s_bps": directional_return,
        "open_interest_change_15m": oi_change,
        "directional_basis_change_5m_bps": basis_change,
        "open_interest_gate_required": 0.0,
    }


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    del router
    original = base._inventory_alignment
    base._inventory_alignment = alignment_without_oi
    try:
        intents, counts = base.detect_session_inventory_acceptance_intents(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
        )
    finally:
        base._inventory_alignment = original
    return intents, {
        "candidate": "candidate-04-session-inventory-acceptance-no-oi-ablation",
        "compiler": "candidate-04-session-inventory-acceptance-no-oi-ablation",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "ablation": {
            "removed_variable": "positive 15-minute open-interest change gate",
            "changed_variables": 1,
            "unchanged": [
                "completed 8-hour session boundary",
                "first penetration closes outside",
                "executed flow alignment",
                "price return alignment",
                "directional basis expansion",
                "later outside-range persistence",
                "confirmation return not larger than break return",
                "boundary invalidation",
                "NautilusTrader execution and 3% NAV risk contract",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
