#!/usr/bin/env python3
"""Compile reversals after an accepted external-break retest resumption fails.

The rejected external-break continuation probe established a useful parent
sequence even though continuation itself lost: an aged/prominent causal pivot
was broken, accepted outside, meaningfully retested, and reclaimed with aligned
break-side flow, return and basis. This compiler does not enter on that reclaim.
It waits for the reclaim attempt itself to fail.

Causal sequence:

1. first accepted break of an aged/prominent right-confirmed causal pool;
2. later non-climactic outside acceptance;
3. first meaningful exact-pool retest with counter-break flow/return;
4. exact pool reclaimed within three completed bars with break-side flow,
   return and basis change;
5. within five later completed bars, price closes back through the exact pool
   into the prior range with counter-break flow, return and basis change;
6. failure displacement is no larger than the original retest shock, excluding
   a new unrelated impact event from being chased.

Inventory is not combined through an OR. The acceptance-to-retest OI change
routes two separately diagnosed mechanisms:

* LIQUIDATION: OI contracts while the accepted break is retested; the break-side
  position base is reduced, and its attempted reclaim then fails.
* FRESH_COUNTER_INVENTORY: OI expands during the retest; new counter-break
  inventory survives the reclaim attempt and forces price back into the range.

No signal is emitted when no OI change was observed between acceptance and
retest. Stops lie beyond the complete reclaim-failure excursion. This compiler
emits intents only; NautilusTrader owns targets, actual fills, fees, positions,
risk, PnL, margin, liquidation and NAV.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401  # warmup-aware loader
import external_break_retest_no_oi_ablation_compiler as parent_compiler
import external_break_retest_compiler as geometry
import rich_signal_compiler_v26b as v26b


Intent = geometry.Intent
FAILURE_BARS = 5
LIQUIDATION_SCENARIO = "FAILED_EXTERNAL_BREAK_RETEST_LIQUIDATION_REVERSAL"
FRESH_INVENTORY_SCENARIO = (
    "FAILED_EXTERNAL_BREAK_RETEST_FRESH_COUNTER_INVENTORY_REVERSAL"
)


def inside_prior_range(close: float, break_side: int, level: float) -> bool:
    """Return True only after price has crossed the exact pool against break."""

    if break_side not in (-1, 1):
        return False
    return break_side * (close - level) < 0.0


def non_impact_failure(
    failure_return_bps: float,
    retest_return_bps: float,
) -> bool:
    if not all(
        math.isfinite(value)
        for value in (failure_return_bps, retest_return_bps)
    ):
        return False
    return 0.0 < failure_return_bps <= retest_return_bps


def inventory_route(interval_oi_change: float) -> str | None:
    """Classify mutually exclusive inventory mechanisms without a threshold."""

    if not math.isfinite(interval_oi_change) or interval_oi_change == 0.0:
        return None
    if interval_oi_change < 0.0:
        return LIQUIDATION_SCENARIO
    return FRESH_INVENTORY_SCENARIO


def detect_failed_break_retest_reversals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    """Wait for the already-completed break-side reclaim to fail causally."""

    parent_intents, parent_summary = parent_compiler.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router=None,
    )
    open_interest = data["metric_sum_open_interest"].astype(float)
    intents: list[Intent] = []
    counts = {
        "completed_parent_reclaims": len(parent_intents),
        "no_state_interval_oi_change": 0,
        "no_exact_reclaim_failure": 0,
        "failure_without_counter_alignment": 0,
        "failure_is_new_impact": 0,
        "liquidation_reversals": 0,
        "fresh_counter_inventory_reversals": 0,
        "duplicate_failure_bars": 0,
    }

    for parent in parent_intents:
        details = dict(parent.details)
        break_side = int(parent.side)
        reversal_side = -break_side
        level = float(details["broken_pool_level"])
        acceptance_index = int(details["acceptance_index"])
        retest_index = int(details["retest_index"])
        reclaim_index = int(details["confirmation_index"])
        interval_oi = v26b.interval_open_interest_change(
            open_interest,
            acceptance_index,
            retest_index,
        )
        scenario = inventory_route(interval_oi)
        if scenario is None:
            counts["no_state_interval_oi_change"] += 1
            continue

        retest_return = float(
            details["retest_counter_break_state"]["directional_return_60s_bps"]
        )
        failure_index: int | None = None
        failure_state: geometry.DirectionalState | None = None
        upper = min(reclaim_index + FAILURE_BARS, len(data) - 2)
        for index in range(reclaim_index + 1, upper + 1):
            row = data.iloc[index]
            if not inside_prior_range(float(row["close"]), break_side, level):
                continue
            state = geometry.directional_state(row, reversal_side)
            if not geometry.aligned_acceptance_state(state):
                counts["failure_without_counter_alignment"] += 1
                break
            assert state is not None
            if not non_impact_failure(state.return_bps, retest_return):
                counts["failure_is_new_impact"] += 1
                break
            failure_index = index
            failure_state = state
            break

        if failure_index is None or failure_state is None:
            counts["no_exact_reclaim_failure"] += 1
            continue
        if data.index[failure_index] > evaluation_end:
            continue

        segment = data.iloc[reclaim_index : failure_index + 1]
        extreme = float(
            segment["low"].min()
            if reversal_side > 0
            else segment["high"].max()
        )
        atr = float(data["atr"].iloc[failure_index])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        stop_level = (
            extreme
            - reversal_side
            * float(impact_parameters.stop_buffer_atr)
            * atr
        )
        routed_details = {
            **details,
            "original_parent_scenario": str(parent.scenario),
            "original_break_side": break_side,
            "reversal_side": reversal_side,
            "inventory_route": (
                "LIQUIDATION"
                if scenario == LIQUIDATION_SCENARIO
                else "FRESH_COUNTER_INVENTORY"
            ),
            "acceptance_to_retest_open_interest_change": interval_oi,
            "reclaim_failure_index": failure_index,
            "reclaim_failure_delay_bars": failure_index - reclaim_index,
            "reclaim_failure_state": {
                "directional_flow_60s": failure_state.flow,
                "directional_return_60s_bps": failure_state.return_bps,
                "directional_basis_change_5m_bps": (
                    failure_state.basis_change_bps
                ),
                "return_to_retest_ratio": (
                    failure_state.return_bps / retest_return
                ),
            },
            "failure_contract": (
                "break-side reclaim lost the exact pool within five completed "
                "bars while counter-break flow, return and basis aligned"
            ),
            "compiler": "candidate-04-failed-external-break-retest-reversal-v1",
        }
        intents.append(
            Intent(
                scenario=scenario,
                side=reversal_side,
                signal_index=failure_index,
                entry_index=failure_index + 1,
                stop_level=stop_level,
                event_indices=tuple(int(value) for value in parent.event_indices)
                + (failure_index,),
                details=routed_details,
            )
        )
        if scenario == LIQUIDATION_SCENARIO:
            counts["liquidation_reversals"] += 1
        else:
            counts["fresh_counter_inventory_reversals"] += 1

    intents.sort(
        key=lambda item: (
            int(item.signal_index),
            0 if item.scenario == LIQUIDATION_SCENARIO else 1,
        )
    )
    unique: list[Intent] = []
    seen: set[int] = set()
    for intent in intents:
        index = int(intent.signal_index)
        if index in seen:
            counts["duplicate_failure_bars"] += 1
            continue
        seen.add(index)
        unique.append(intent)

    counts.update(
        {
            f"parent_{key}": int(value)
            for key, value in dict(parent_summary.get("route_counts", {})).items()
        }
    )
    return unique, counts


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    intents, counts = detect_failed_break_retest_reversals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-failed-external-break-retest-reversal-v1",
        "compiler": "candidate-04-failed-external-break-retest-reversal-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "parent": (
                "accepted causal external-pool break, first exact retest and "
                "completed break-side reclaim"
            ),
            "failure": (
                "exact pool lost into the prior range within five later "
                "completed bars with counter-break flow, return and basis"
            ),
            "relative_impact": (
                "failure return is positive and no larger than the original "
                "counter-break retest shock"
            ),
            "inventory_routes": {
                "liquidation": (
                    "OI contracts between accepted outside bar and retest"
                ),
                "fresh_counter_inventory": (
                    "OI expands between accepted outside bar and retest"
                ),
            },
            "invalidation": (
                "complete reclaim-to-failure excursion plus ATR buffer"
            ),
            "target_and_execution": (
                "causal opposite-side external liquidity through NautilusTrader"
            ),
        },
        "constants": {
            "failure_bars": FAILURE_BARS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
