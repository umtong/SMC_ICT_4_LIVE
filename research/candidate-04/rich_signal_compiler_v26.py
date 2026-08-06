#!/usr/bin/env python3
"""V26: preserve V25 and add trapped countertrend inventory retests.

The V25 states remain unchanged. This compiler adds one independent continuation
mechanism after an accepted causal external-pool break:

1. an aged/prominent right-confirmed pool is broken and accepted outside;
2. the first meaningful retest moves against the break with completed aligned
   flow, return and basis change;
3. open interest increases during that counter-break retest, identifying fresh
   countertrend inventory rather than liquidation of the break-side position;
4. at the retest close, both the completed 240-minute price displacement and
   the absolute futures/index basis remain aligned with the original break;
5. the exact pool is reclaimed within three completed bars with break-side
   flow, return and basis change, and reclaim displacement is no larger than the
   retest shock.

The economic state is therefore not merely "OI increased". Countertrend traders
added inventory during a pullback, but neither the parent auction nor derivative
basis accepted their direction; the exact broken boundary is then reclaimed.

This module emits completed-data intents only. NautilusTrader remains the sole
owner of orders, actual fills, fees, positions, PnL, margin, liquidation and NAV.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401  # warmup-aware loader
import rich_signal_compiler_v25 as v25
import external_break_retest_compiler as retest


Intent = v25.Intent
SCENARIO = "TRAPPED_COUNTERTREND_INVENTORY_RETEST_RESUMPTION"
PARENT_BARS = 240


def completed_return_bps(
    close: pd.Series,
    index: int,
    bars: int = PARENT_BARS,
) -> float:
    """Return a completed trailing displacement ending at ``index``."""

    if index < bars:
        return float("nan")
    start = float(close.iloc[index - bars])
    end = float(close.iloc[index])
    if not all(math.isfinite(value) and value > 0.0 for value in (start, end)):
        return float("nan")
    return (end / start - 1.0) * 10_000.0


def is_trapped_countertrend_inventory(
    oi_change_15m: float,
    side: int,
    trade_index_basis_bps: float,
    parent_return_bps: float,
) -> bool:
    """Classify fresh countertrend inventory which failed to change the parent.

    ``side`` is the accepted break direction. A positive OI change during the
    counter-break retest indicates new inventory entered during the pullback.
    The break remains structurally accepted only when both completed parent
    displacement and the absolute futures/index basis still point with ``side``.
    """

    values = (oi_change_15m, trade_index_basis_bps, parent_return_bps)
    if side not in (-1, 1) or not all(math.isfinite(value) for value in values):
        return False
    return (
        oi_change_15m > 0.0
        and side * trade_index_basis_bps > 0.0
        and side * parent_return_bps > 0.0
    )


def _copy_inventory_intent(
    parent: Intent,
    *,
    oi_change_15m: float,
    basis_bps: float,
    parent_return_bps: float,
) -> Intent:
    details = dict(parent.details)
    counter = dict(details["retest_counter_break_state"])
    counter["open_interest_change_15m"] = oi_change_15m
    counter["inventory_interpretation"] = "fresh countertrend inventory"
    details["retest_counter_break_state"] = counter
    details.update(
        {
            "retest_trade_index_basis_bps": basis_bps,
            "retest_parent_240m_return_bps": parent_return_bps,
            "break_side_basis_bps": int(parent.side) * basis_bps,
            "break_side_parent_240m_return_bps": (
                int(parent.side) * parent_return_bps
            ),
            "inventory_state": (
                "countertrend OI expansion failed to overturn the accepted "
                "parent auction and basis"
            ),
            "compiler": "candidate-04-v26",
        }
    )
    return Intent(
        scenario=SCENARIO,
        side=int(parent.side),
        signal_index=int(parent.signal_index),
        entry_index=int(parent.entry_index),
        stop_level=float(parent.stop_level),
        event_indices=tuple(int(value) for value in parent.event_indices),
        details=details,
    )


def detect_trapped_countertrend_inventory_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    """Run the accepted-break detector, then route OI mechanisms explicitly."""

    actual_oi = data["oi_change_xday_15m"].astype(float).copy()
    probe = data.copy()
    # The base detector's negative-OI gate is bypassed only to expose all
    # otherwise-identical accepted break/retest events. Actual OI is restored
    # below and becomes a positive-inventory requirement, not an execution input.
    probe["oi_change_xday_15m"] = -1.0
    parents, base_counts = retest.detect_external_break_retest_intents(
        probe,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )

    close = data["close"].astype(float)
    basis = data["trade_index_basis_bps"].astype(float)
    accepted: list[Intent] = []
    counts = {
        "base_completed_break_retests": len(parents),
        "oi_not_expanding": 0,
        "basis_not_break_aligned": 0,
        "parent_not_break_aligned": 0,
        "trapped_countertrend_inventory": 0,
    }

    for parent in parents:
        retest_index = int(parent.details["retest_index"])
        oi_change = float(actual_oi.iloc[retest_index])
        basis_bps = float(basis.iloc[retest_index])
        parent_return = completed_return_bps(close, retest_index)
        side = int(parent.side)

        if not math.isfinite(oi_change) or oi_change <= 0.0:
            counts["oi_not_expanding"] += 1
            continue
        if not math.isfinite(basis_bps) or side * basis_bps <= 0.0:
            counts["basis_not_break_aligned"] += 1
            continue
        if not math.isfinite(parent_return) or side * parent_return <= 0.0:
            counts["parent_not_break_aligned"] += 1
            continue
        if not is_trapped_countertrend_inventory(
            oi_change,
            side,
            basis_bps,
            parent_return,
        ):
            continue

        accepted.append(
            _copy_inventory_intent(
                parent,
                oi_change_15m=oi_change,
                basis_bps=basis_bps,
                parent_return_bps=parent_return,
            )
        )
        counts["trapped_countertrend_inventory"] += 1

    counts.update(
        {
            f"base_{key}": int(value)
            for key, value in base_counts.items()
        }
    )
    return accepted, counts


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    v25_intents, v25_summary = v25.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    inventory_intents, inventory_counts = (
        detect_trapped_countertrend_inventory_intents(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
        )
    )

    # Preserve every V25 decision. The new scenario may fill an otherwise-empty
    # signal bar, but it never replaces a known V25 state on the same completed bar.
    combined = list(v25_intents)
    seen = {int(intent.signal_index) for intent in v25_intents}
    overlaps = 0
    for intent in inventory_intents:
        if int(intent.signal_index) in seen:
            overlaps += 1
            continue
        combined.append(intent)
        seen.add(int(intent.signal_index))

    priority = {
        "EXTERNAL_POOL_FAILED_DISCOVERY_REVERSAL": 0,
        SCENARIO: 1,
        "NON_CLIMACTIC_PARENT_AUCTION_RESUMPTION": 2,
        "NORMAL_FAILED_AUCTION_RESUMPTION": 3,
        "TAIL_CONFIRMED_STRESS_FAILED_AUCTION": 4,
        "ORDERLY_INVENTORY_DISPLACEMENT": 5,
    }
    combined.sort(
        key=lambda item: (
            int(item.signal_index),
            priority.get(item.scenario, 99),
        )
    )

    route_counts = dict(v25_summary.get("route_counts", {}))
    route_counts.update(inventory_counts)
    route_counts["inventory_signal_bar_overlaps_with_v25"] = overlaps
    route_counts["inventory_signals_added"] = len(combined) - len(v25_intents)
    return combined, {
        **v25_summary,
        "candidate": "candidate-04-v26-trapped-countertrend-inventory-router",
        "compiler": "candidate-04-v26",
        "raw_routed_signals": len(combined),
        "unique_signal_bars": len(combined),
        "route_counts": route_counts,
        "inventory_retest_contract": {
            "parent_creation": (
                "accepted first break of an aged/prominent causal pivot pool"
            ),
            "countertrend_inventory": (
                "positive 15-minute OI change during the first exact retest"
            ),
            "failed_acceptance": (
                "at the retest close, completed 240-minute displacement and "
                "absolute futures/index basis remain aligned with the break"
            ),
            "reclaim": (
                "exact broken pool reclaimed within three completed bars with "
                "break-side flow return and basis change"
            ),
            "invalidation": "complete retest/reclaim extreme plus ATR buffer",
            "target_and_execution": "causal external liquidity through NautilusTrader",
            "changed_mechanisms_from_v25": 1,
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
