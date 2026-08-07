#!/usr/bin/env python3
"""Single-component ablation of V43: remove impact innovation residual only.

All V43 states, parent-session boundaries, first-take semantics, opposing-depth
pressure floor, real flow/return/basis alignment, inventory route, exact reclaim,
independent retest/resumption, causal targets, stops, risk, costs and Nautilus
execution remain unchanged. The removed variable is the residual z-score of
realized impact relative to the rolling past flow/depth model.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import parent_session_liquidity_transfer_compiler as v43
import rich_signal_compiler_v22 as v22

Intent = v43.Intent


def direct_flow_aligned(
    impact: pd.DataFrame,
    data: pd.DataFrame,
    index: int,
    side: int,
    minimum_z: float,
) -> bool:
    """Preserve pressure and directional response, ablate innovation residual."""

    del minimum_z
    values = (
        v43.finite(impact["signed_pressure"].iloc[index]),
        v43.finite(impact["absolute_pressure"].iloc[index]),
        v43.finite(impact["pressure_cutoff"].iloc[index]),
        v43.finite(data["flow_60s"].iloc[index]),
        v43.finite(data["ret_60s_bps"].iloc[index]),
        v43.finite(data["basis_change_1m"].iloc[index]),
    )
    if not all(math.isfinite(value) for value in values):
        return False
    pressure, absolute, cutoff, flow, ret, basis = values
    return (
        side * pressure > 0.0
        and absolute >= cutoff
        and side * flow > 0.0
        and side * ret > 0.0
        and side * basis > 0.0
    )


# Reuse V43's completed scenario resolvers with one conceptual variable removed.
v43._impact_aligned = direct_flow_aligned


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    impact = v43.impact_state(data)
    sessions = v43.completed_parent_sessions(data)
    snapshots = v43.active_causal_pool_snapshots(data, config)
    boundary_takes, attacks = v43.parent_boundary_first_takes(
        data,
        sessions,
        config,
    )
    oi_5m = data["metric_sum_open_interest"].astype(float).pct_change(
        5,
        fill_method=None,
    )
    oi_cutoff = v43._shifted_quantile(
        oi_5m.abs(),
        v43.OI_QUANTILE,
        v43.OI_WINDOW,
        v43.OI_MINIMUM,
    )

    intents: list[Intent] = []
    counts = {
        "parent_boundary_attacks": len(attacks),
        "impact_model_not_ready": 0,
        "insufficient_pressure": 0,
        "routed_inventory_attacks": 0,
        "unrouted_inventory_attacks": 0,
        "reversal_attempts": 0,
        "reversal_confirmations": 0,
        "continuation_attempts": 0,
        "continuation_confirmations": 0,
        "signals": 0,
    }
    for attack_index, (attack_side, boundary) in sorted(attacks.items()):
        if data.index[attack_index] > evaluation_end:
            break
        values = (
            v43.finite(impact["absolute_pressure"].iloc[attack_index]),
            v43.finite(impact["pressure_cutoff"].iloc[attack_index]),
            v43.finite(impact["signed_pressure"].iloc[attack_index]),
        )
        if not all(math.isfinite(value) for value in values):
            counts["impact_model_not_ready"] += 1
            continue
        absolute, cutoff, pressure = values
        if absolute < cutoff or attack_side * pressure <= 0.0:
            counts["insufficient_pressure"] += 1
            continue

        route, inventory_details = v43._attack_inventory_route(
            data,
            impact,
            attack_index,
            attack_side,
            oi_cutoff,
        )
        if route is None:
            counts["unrouted_inventory_attacks"] += 1
            continue
        counts["routed_inventory_attacks"] += 1

        # Exact failed-auction resolution is the stronger cause and is evaluated
        # before generic acceptance, preserving V43 mutual exclusivity.
        counts["reversal_attempts"] += 1
        intent = v43.resolve_reversal(
            data,
            impact,
            snapshots,
            sessions,
            boundary_takes,
            attack_index,
            attack_side,
            boundary,
            route,
            inventory_details,
            evaluation_end,
            config,
            impact_parameters,
        )
        if intent is not None:
            counts["reversal_confirmations"] += 1
            intents.append(intent)
            continue

        close = v43.finite(data["close"].iloc[attack_index])
        accepted_outside = (
            close > boundary if attack_side > 0 else close < boundary
        )
        basis_aligned = (
            attack_side
            * v43.finite(data["basis_change_1m"].iloc[attack_index])
            > 0.0
        )
        if not (
            route == "NEW_INVENTORY"
            and accepted_outside
            and basis_aligned
        ):
            continue
        counts["continuation_attempts"] += 1
        intent = v43.resolve_continuation(
            data,
            impact,
            snapshots,
            sessions,
            boundary_takes,
            attack_index,
            attack_side,
            boundary,
            inventory_details,
            evaluation_end,
            config,
            impact_parameters,
        )
        if intent is not None:
            counts["continuation_confirmations"] += 1
            intents.append(intent)

    priority = {
        v43.SESSION_REVERSAL_NEW: 0,
        v43.SESSION_REVERSAL_LIQUIDATION: 1,
        v43.SESSION_REVERSAL_PASSIVE: 2,
        v43.SESSION_CONTINUATION: 3,
    }
    intents.sort(
        key=lambda item: (int(item.signal_index), priority[item.scenario])
    )
    unique: list[Intent] = []
    seen: set[int] = set()
    for intent in intents:
        index = int(intent.signal_index)
        if index in seen:
            continue
        seen.add(index)
        unique.append(intent)
    counts["signals"] = len(unique)
    return unique, {
        "candidate": "candidate-04-v43-no-impact-innovation-ablation",
        "compiler": "candidate-04-v43-no-impact-innovation-ablation",
        "ablated_component": "depth_conditioned_impact_innovation_residual_z_score",
        "preserved_components": [
            "completed_parent_session_boundaries",
            "first_boundary_take",
            "opposing_depth_pressure_floor",
            "directional_flow_return_basis_alignment",
            "inventory_route",
            "exact_boundary_reclaim",
            "independent_retest_and_resumption",
            "compiler_declared_pre_signal_causal_target",
            "complete_excursion_stop",
            "NautilusTrader_execution_and_current_NAV_3pct_risk",
        ],
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": len(intents) - len(unique),
        "route_counts": counts,
    }


v22.collect_signals = collect_signals

if __name__ == "__main__":
    v22.main()
