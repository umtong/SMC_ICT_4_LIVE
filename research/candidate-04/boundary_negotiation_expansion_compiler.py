#!/usr/bin/env python3
"""Trade settled expansion after a completed two-sided boundary negotiation.

V29 entered the first reclaim. V30 waited one additional aligned close, but the
untouched-week directions were still stopped inside repeated boundary crossing.
The direction was not the principal error: all three episodes later expanded in
the intended direction. The incomplete state was timing and invalidation.

This compiler replaces only three early-reclaim parent routes with a complete
negotiation state:

1. a V29 parent identifies an objective boundary and first reclaim/acceptance;
2. completed closes must cross that boundary at least twice, proving both sides
   participated rather than treating one bar as a regime;
3. the current completed close must leave the entire prior close-negotiation
   range in one direction;
4. executed flow, 60-second return and futures-index basis must align; and
5. the expansion return must reach the shifted, past-only q95 already specified
   by the configuration.

The stop is beyond the full high/low negotiation range, not the incomplete last
reclaim segment. Inventory and parent-auction relations determine whether the
settled direction represents continuation or failure. Other V29 mechanisms are
unchanged. This module emits intents only; NautilusTrader owns targets, orders,
fills, costs, positions, risk, liquidation and NAV.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v29 as v29
import two_stage_auction_resolution_compiler as v30


Intent = v22.Intent
ROUTED_PARENTS = set(v30.ROUTED_PARENTS)
MIN_BOUNDARY_TRANSITIONS = 2

SCENARIOS = {
    v30.BALANCED_PARENT: {
        "parent_side": "BALANCED_SETTLED_FAILED_INVENTORY_REVERSAL",
        "opposite_side": "BALANCED_SETTLED_BREAKOUT_CONTINUATION",
    },
    v30.STRESS_PARENT: {
        "parent_side": "STRESS_SETTLED_ACCEPTANCE_CONTINUATION",
        "opposite_side": "STRESS_SETTLED_DELEVERAGING_REVERSAL",
    },
    v30.EXTERNAL_PARENT: {
        "parent_side": "EXTERNAL_SETTLED_FAILED_DISCOVERY_REVERSAL",
        "opposite_side": "PARENT_ALIGNED_IMPACT_SETTLED_CONTINUATION",
    },
}


def past_only_displacement_cutoff(
    data: pd.DataFrame,
    config: Any,
) -> pd.Series:
    absolute_return = data["ret_60s_bps"].astype(float).abs()
    return (
        absolute_return.shift(1)
        .rolling(
            int(config.stress_inventory_quantile_window_minutes),
            min_periods=int(config.stress_inventory_quantile_min_periods),
        )
        .quantile(float(config.stress_inventory_quantile))
    )


def close_side(close: float, boundary: float, previous: int) -> int:
    if not math.isfinite(close) or not math.isfinite(boundary):
        return previous
    if close > boundary:
        return 1
    if close < boundary:
        return -1
    return previous


def aligned_expansion(row: pd.Series, side: int) -> dict[str, float] | None:
    if side not in (-1, 1):
        return None
    flow = side * v30.finite_number(row["flow_60s"])
    return_bps = side * v30.finite_number(row["ret_60s_bps"])
    basis = side * v30.finite_number(row["basis_change_5m"])
    if not all(math.isfinite(value) for value in (flow, return_bps, basis)):
        return None
    if flow <= 0.0 or return_bps <= 0.0 or basis <= 0.0:
        return None
    return {
        "directional_flow_60s": flow,
        "directional_return_60s_bps": return_bps,
        "directional_basis_change_5m_bps": basis,
    }


def _open_interest(data: pd.DataFrame, index: int) -> float:
    if not 0 <= index < len(data):
        return float("nan")
    return v30.finite_number(data["metric_sum_open_interest"].iloc[index])


def settled_cause(
    data: pd.DataFrame,
    parent: Intent,
    expansion_index: int,
    expansion_side: int,
) -> tuple[bool, dict[str, Any]]:
    scenario = str(parent.scenario)
    parent_side = int(parent.side)
    expansion_oi = _open_interest(data, expansion_index)

    if scenario == v30.BALANCED_PARENT:
        pool_side = int(parent.details.get("pool_side", 0))
        attack_index = int(parent.details.get("attack_index", -1))
        attack_oi = _open_interest(data, attack_index)
        reclaim_oi = _open_interest(data, int(parent.signal_index))
        if expansion_side == pool_side:
            cutoff = v30.finite_number(
                parent.details.get("past_only_positive_oi_step_median")
            )
            interval_change = (
                expansion_oi / attack_oi - 1.0
                if attack_oi > 0.0 and math.isfinite(expansion_oi)
                else float("nan")
            )
            passed = (
                math.isfinite(interval_change)
                and math.isfinite(cutoff)
                and cutoff > 0.0
                and interval_change >= cutoff
            )
            return passed, {
                "settled_inventory_route": "BREAKOUT_INVENTORY_CONTINUATION",
                "attack_open_interest": attack_oi,
                "settled_expansion_open_interest": expansion_oi,
                "attack_to_expansion_open_interest_change": interval_change,
                "material_positive_oi_cutoff": cutoff,
                "material_breakout_inventory_persists": passed,
            }
        if expansion_side == -pool_side:
            passed = (
                math.isfinite(expansion_oi)
                and math.isfinite(reclaim_oi)
                and expansion_oi <= reclaim_oi
            )
            return passed, {
                "settled_inventory_route": "FAILED_BREAKOUT_INVENTORY_REVERSAL",
                "first_reclaim_open_interest": reclaim_oi,
                "settled_expansion_open_interest": expansion_oi,
                "breakout_inventory_no_longer_expanding": passed,
            }
        return False, {"settled_inventory_route": "INVALID_POOL_SIDE"}

    if scenario == v30.STRESS_PARENT:
        parent_index = int(parent.details.get("parent_reversal_signal_index", -1))
        parent_oi = _open_interest(data, parent_index)
        if expansion_side == parent_side:
            return True, {
                "settled_stress_route": "ACCEPTANCE_CONTINUATION",
                "parent_open_interest": parent_oi,
                "settled_expansion_open_interest": expansion_oi,
            }
        passed = (
            math.isfinite(parent_oi)
            and math.isfinite(expansion_oi)
            and expansion_oi <= parent_oi
        )
        return passed, {
            "settled_stress_route": "DELEVERAGING_REVERSAL",
            "parent_open_interest": parent_oi,
            "settled_expansion_open_interest": expansion_oi,
            "deleveraging_not_new_inventory": passed,
        }

    if scenario == v30.EXTERNAL_PARENT:
        shock_side = int(parent.details.get("shock_side", 0))
        reversal_allowed = v30.external_reversal_allowed(parent.details)
        if expansion_side == parent_side:
            passed = reversal_allowed
            route = "FAILED_DISCOVERY_REVERSAL"
        elif expansion_side == shock_side:
            passed = not reversal_allowed
            route = "PARENT_ALIGNED_IMPACT_CONTINUATION"
        else:
            passed = False
            route = "INVALID_EXTERNAL_RELATION"
        return passed, {
            "settled_external_route": route,
            "external_reversal_allowed": reversal_allowed,
            "shock_side": shock_side,
            "pre_shock_parent_480m_return_bps": parent.details.get(
                "pre_shock_parent_480m_return_bps"
            ),
            "impact_absolute_return_bps": parent.details.get(
                "impact_absolute_return_bps"
            ),
        }

    return False, {"settled_route": "UNSUPPORTED_PARENT"}


def _scenario_name(parent: Intent, expansion_side: int) -> str:
    relation = (
        "parent_side" if expansion_side == int(parent.side) else "opposite_side"
    )
    return SCENARIOS[str(parent.scenario)][relation]


def resolve_negotiation(
    data: pd.DataFrame,
    parent: Intent,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    cutoff: pd.Series,
) -> tuple[Intent | None, str]:
    boundary = v30.objective_boundary(parent)
    if not math.isfinite(boundary):
        return None, "invalid_boundary"
    parent_index = int(parent.signal_index)
    initial_close = v30.finite_number(data["close"].iloc[parent_index])
    previous_side = close_side(initial_close, boundary, int(parent.side))
    transitions = 0
    maximum_wait = int(config.stress_failure_wait_minutes)
    upper = min(parent_index + maximum_wait, len(data) - 2)

    for index in range(parent_index + 1, upper + 1):
        if data.index[index] > evaluation_end:
            break
        row = data.iloc[index]
        close = v30.finite_number(row["close"])
        current_side = close_side(close, boundary, previous_side)
        if current_side != previous_side:
            transitions += 1
            previous_side = current_side
        if transitions < MIN_BOUNDARY_TRANSITIONS:
            continue

        prior = data.iloc[parent_index:index]
        if prior.empty:
            continue
        prior_close_high = v30.finite_number(prior["close"].max())
        prior_close_low = v30.finite_number(prior["close"].min())
        expansion_side = (
            1
            if close > prior_close_high
            else -1
            if close < prior_close_low
            else 0
        )
        if expansion_side == 0:
            continue
        state = aligned_expansion(row, expansion_side)
        if state is None:
            continue
        threshold = v30.finite_number(cutoff.iloc[index])
        absolute_return = abs(v30.finite_number(row["ret_60s_bps"]))
        if not (
            math.isfinite(threshold)
            and threshold > 0.0
            and math.isfinite(absolute_return)
            and absolute_return >= threshold
        ):
            continue
        cause_passed, cause_details = settled_cause(
            data,
            parent,
            index,
            expansion_side,
        )
        if not cause_passed:
            continue

        negotiation = data.iloc[parent_index : index + 1]
        atr = v30.finite_number(row["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        full_low = v30.finite_number(negotiation["low"].min())
        full_high = v30.finite_number(negotiation["high"].max())
        stop = (
            full_low - float(impact_parameters.stop_buffer_atr) * atr
            if expansion_side > 0
            else full_high + float(impact_parameters.stop_buffer_atr) * atr
        )
        if expansion_side * (close - stop) <= 0.0:
            continue
        details = {
            **parent.details,
            **cause_details,
            **state,
            "parent_scenario": str(parent.scenario),
            "parent_signal_index": parent_index,
            "parent_side": int(parent.side),
            "negotiation_boundary": boundary,
            "negotiation_start_index": parent_index,
            "negotiation_expansion_index": index,
            "negotiation_bars": index - parent_index + 1,
            "boundary_transitions": transitions,
            "minimum_boundary_transitions": MIN_BOUNDARY_TRANSITIONS,
            "prior_close_range_low": prior_close_low,
            "prior_close_range_high": prior_close_high,
            "full_negotiation_low": full_low,
            "full_negotiation_high": full_high,
            "settled_expansion_side": expansion_side,
            "settled_expansion_absolute_return_60s_bps": absolute_return,
            "past_only_settled_displacement_cutoff_bps": threshold,
            "settled_displacement_quantile": float(
                config.stress_inventory_quantile
            ),
            "compiler": "candidate-04-boundary-negotiation-expansion",
        }
        return (
            Intent(
                scenario=_scenario_name(parent, expansion_side),
                side=expansion_side,
                signal_index=index,
                entry_index=index + 1,
                stop_level=stop,
                event_indices=tuple(
                    [*tuple(int(value) for value in parent.event_indices), index]
                ),
                details=details,
            ),
            "settled_expansion",
        )
    return None, "unresolved"


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    parents, parent_summary = v29.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    cutoff = past_only_displacement_cutoff(data, config)
    accepted: list[Intent] = []
    counts: dict[str, Any] = {
        "parents_routed": 0,
        "parents_unchanged": 0,
        "settled_expansions": 0,
        "unresolved": 0,
        "invalid_boundary": 0,
        "by_parent": {},
    }
    for parent in parents:
        scenario = str(parent.scenario)
        if scenario not in ROUTED_PARENTS:
            accepted.append(parent)
            counts["parents_unchanged"] += 1
            continue
        counts["parents_routed"] += 1
        by_parent = counts["by_parent"].setdefault(
            scenario,
            {"routed": 0, "settled_expansion": 0, "unresolved": 0},
        )
        by_parent["routed"] += 1
        resolved, outcome = resolve_negotiation(
            data,
            parent,
            evaluation_end,
            config,
            impact_parameters,
            cutoff,
        )
        if resolved is not None:
            accepted.append(resolved)
            counts["settled_expansions"] += 1
            by_parent["settled_expansion"] += 1
        else:
            counts[outcome] = int(counts.get(outcome, 0)) + 1
            by_parent["unresolved"] += 1

    accepted.sort(key=lambda item: int(item.signal_index))
    unique: list[Intent] = []
    seen: set[int] = set()
    duplicate_bars = 0
    for intent in accepted:
        index = int(intent.signal_index)
        if index in seen:
            duplicate_bars += 1
            continue
        seen.add(index)
        unique.append(intent)
    counts["duplicate_signal_bars"] = duplicate_bars

    router_contract = dict(parent_summary.get("router_contract", {}))
    router_contract["settled_boundary_negotiation"] = {
        "routed_parents": sorted(ROUTED_PARENTS),
        "two_sided_requirement": (
            "at least two completed-close transitions across the objective boundary"
        ),
        "settled_expansion": (
            "current completed close leaves the entire prior close-negotiation "
            "range with flow, return and futures-index basis aligned"
        ),
        "displacement": (
            "absolute completed 60-second return >= shifted past-only configured q95"
        ),
        "invalidation": "opposite full high/low negotiation extreme plus causal ATR buffer",
    }
    return unique, {
        **parent_summary,
        "candidate": "candidate-04-v31-boundary-negotiation-expansion",
        "compiler": "candidate-04-v31-boundary-negotiation-expansion",
        "raw_routed_signals": len(unique),
        "unique_signal_bars": len(unique),
        "negotiation_route_counts": counts,
        "router_contract": router_contract,
        "changes_from_v29": {
            "changed_entry_state": 1,
            "change": (
                "three first-reclaim routes now require completed two-sided "
                "negotiation and q95 expansion beyond the full prior close range"
            ),
            "execution": "NautilusTrader BacktestNode",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
