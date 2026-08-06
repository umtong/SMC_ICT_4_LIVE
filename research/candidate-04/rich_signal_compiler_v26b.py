#!/usr/bin/env python3
"""V26b: causally measure inventory created during the retest interval.

V26 intended to identify fresh countertrend inventory added after an accepted
external-pool break. Its implementation used a trailing 15-minute OI change at
the retest bar. When the retest occurred shortly after acceptance, that window
contained OI changes which predated the accepted parent state and could not be
attributed to the retest.

V26b changes only that measurement:

    OI change during retest
    = OI(first meaningful retest) / OI(accepted outside bar) - 1

All pool, break, acceptance, retest, reclaim, basis, parent-displacement, stop,
target, risk and execution rules remain unchanged. A zero value is not fresh
inventory; it commonly means no new exchange OI observation arrived between the
accepted break and the retest. This module emits intents only. NautilusTrader
remains the sole owner of orders, fills, fees, positions, PnL, margin,
liquidation and NAV.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v25 as v25
import rich_signal_compiler_v26 as v26
import external_break_retest_compiler as retest


Intent = v26.Intent
SCENARIO = v26.SCENARIO


def interval_open_interest_change(
    open_interest: pd.Series,
    acceptance_index: int,
    retest_index: int,
) -> float:
    """Return OI change observed strictly over the accepted-state retest span."""

    if acceptance_index < 0 or retest_index <= acceptance_index:
        return float("nan")
    if retest_index >= len(open_interest):
        return float("nan")
    start = float(open_interest.iloc[acceptance_index])
    end = float(open_interest.iloc[retest_index])
    if not all(math.isfinite(value) and value > 0.0 for value in (start, end)):
        return float("nan")
    return end / start - 1.0


def is_causal_trapped_countertrend_inventory(
    interval_oi_change: float,
    side: int,
    trade_index_basis_bps: float,
    parent_return_bps: float,
) -> bool:
    values = (
        interval_oi_change,
        trade_index_basis_bps,
        parent_return_bps,
    )
    if side not in (-1, 1) or not all(math.isfinite(value) for value in values):
        return False
    return (
        interval_oi_change > 0.0
        and side * trade_index_basis_bps > 0.0
        and side * parent_return_bps > 0.0
    )


def _copy_intent(
    parent: Intent,
    *,
    interval_oi_change: float,
    rolling_oi_change_15m: float,
    acceptance_oi: float,
    retest_oi: float,
    basis_bps: float,
    parent_return_bps: float,
) -> Intent:
    details = dict(parent.details)
    counter = dict(details["retest_counter_break_state"])
    counter.update(
        {
            "open_interest_change_15m": rolling_oi_change_15m,
            "acceptance_open_interest": acceptance_oi,
            "retest_open_interest": retest_oi,
            "acceptance_to_retest_open_interest_change": interval_oi_change,
            "inventory_interpretation": (
                "fresh countertrend inventory observed after acceptance"
            ),
        }
    )
    details["retest_counter_break_state"] = counter
    details.update(
        {
            "retest_trade_index_basis_bps": basis_bps,
            "retest_parent_240m_return_bps": parent_return_bps,
            "break_side_basis_bps": int(parent.side) * basis_bps,
            "break_side_parent_240m_return_bps": (
                int(parent.side) * parent_return_bps
            ),
            "inventory_measurement": (
                "OI(retest) / OI(accepted outside bar) - 1"
            ),
            "implementation_fix_from_v26": (
                "removed pre-acceptance contamination from trailing 15m OI"
            ),
            "compiler": "candidate-04-v26b",
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


def detect_causal_trapped_inventory_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    probe = data.copy()
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
    open_interest = data["metric_sum_open_interest"].astype(float)
    rolling_oi = data["oi_change_xday_15m"].astype(float)
    accepted: list[Intent] = []
    counts = {
        "base_completed_break_retests": len(parents),
        "no_post_acceptance_oi_increase": 0,
        "basis_not_break_aligned": 0,
        "parent_not_break_aligned": 0,
        "causal_trapped_countertrend_inventory": 0,
    }

    for parent in parents:
        acceptance_index = int(parent.details["acceptance_index"])
        retest_index = int(parent.details["retest_index"])
        interval_change = interval_open_interest_change(
            open_interest,
            acceptance_index,
            retest_index,
        )
        basis_bps = float(basis.iloc[retest_index])
        parent_return = v26.completed_return_bps(close, retest_index)
        side = int(parent.side)

        if not math.isfinite(interval_change) or interval_change <= 0.0:
            counts["no_post_acceptance_oi_increase"] += 1
            continue
        if not math.isfinite(basis_bps) or side * basis_bps <= 0.0:
            counts["basis_not_break_aligned"] += 1
            continue
        if not math.isfinite(parent_return) or side * parent_return <= 0.0:
            counts["parent_not_break_aligned"] += 1
            continue
        if not is_causal_trapped_countertrend_inventory(
            interval_change,
            side,
            basis_bps,
            parent_return,
        ):
            continue

        accepted.append(
            _copy_intent(
                parent,
                interval_oi_change=interval_change,
                rolling_oi_change_15m=float(rolling_oi.iloc[retest_index]),
                acceptance_oi=float(open_interest.iloc[acceptance_index]),
                retest_oi=float(open_interest.iloc[retest_index]),
                basis_bps=basis_bps,
                parent_return_bps=parent_return,
            )
        )
        counts["causal_trapped_countertrend_inventory"] += 1

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
    inventory_intents, inventory_counts = detect_causal_trapped_inventory_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )

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
        "candidate": "candidate-04-v26b-causal-inventory-retest-router",
        "compiler": "candidate-04-v26b",
        "raw_routed_signals": len(combined),
        "unique_signal_bars": len(combined),
        "route_counts": route_counts,
        "inventory_retest_contract": {
            "parent_creation": (
                "accepted first break of an aged/prominent causal pivot pool"
            ),
            "countertrend_inventory": (
                "open interest increases between the accepted outside bar and "
                "the first meaningful exact-pool retest"
            ),
            "failed_acceptance": (
                "at retest, completed 240-minute displacement and absolute "
                "futures/index basis remain aligned with the break"
            ),
            "reclaim": (
                "exact pool reclaimed within three completed bars with "
                "break-side flow return and basis change"
            ),
            "implementation_fix": (
                "trailing 15-minute OI was replaced by state-interval OI; all "
                "other V26 variables are unchanged"
            ),
            "target_and_execution": "causal external liquidity through NautilusTrader",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
