#!/usr/bin/env python3
"""Resolve first-reclaim candidates with a second completed auction state.

V29 failed its first untouched week because three unrelated first-reclaim
patterns were all treated as final auction resolution. This module does not add
a score and does not inspect PnL. It replaces only those three early-entry
routes with a causal two-stage state machine:

1. a parent detector emits the first completed reclaim/acceptance candidate;
2. later completed data must resolve the same objective boundary;
3. if price remains on the intended side with executed flow, return and
   futures-index basis aligned, the original scenario is admitted;
4. if price reaccepts the opposite side with the opposite alignment, an
   economically distinct failure route is admitted only when its inventory or
   parent-auction cause is present.

The three routed states are:

* balanced-session failed breakout inventory;
* tail-confirmed stress failed-auction acceptance;
* external-pool failed price discovery.

Other V29 mechanisms are left unchanged. The compiler emits timestamped intents
only. NautilusTrader remains the sole owner of targets, orders, fills, fees,
positions, PnL, margin, liquidation and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v29 as v29


Intent = v22.Intent
MAX_RESOLUTION_BARS = 180

BALANCED_PARENT = "BALANCED_SESSION_FAILED_INVENTORY_BREAKOUT_REVERSAL"
STRESS_PARENT = "TAIL_CONFIRMED_STRESS_FAILED_AUCTION"
EXTERNAL_PARENT = "EXTERNAL_POOL_FAILED_DISCOVERY_REVERSAL"
ROUTED_PARENTS = {BALANCED_PARENT, STRESS_PARENT, EXTERNAL_PARENT}

HOLD_SCENARIOS = {
    BALANCED_PARENT: "TWO_STAGE_BALANCED_FAILED_INVENTORY_REVERSAL_HOLD",
    STRESS_PARENT: "TWO_STAGE_STRESS_FAILED_AUCTION_HOLD",
    EXTERNAL_PARENT: "TWO_STAGE_EXTERNAL_FAILED_DISCOVERY_HOLD",
}
FAILURE_SCENARIOS = {
    BALANCED_PARENT: "BALANCED_RECLAIM_FAILURE_INVENTORY_CONTINUATION",
    STRESS_PARENT: "STRESS_ACCEPTANCE_FAILURE_DELEVERAGING_REVERSAL",
    EXTERNAL_PARENT: "PARENT_ALIGNED_EXTERNAL_RECLAIM_FAILURE_CONTINUATION",
}


@dataclass(frozen=True, slots=True)
class AlignedState:
    side: int
    flow_60s: float
    return_60s_bps: float
    basis_change_5m_bps: float


def finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def aligned_state(row: pd.Series, side: int) -> AlignedState | None:
    """Return a completed directionally aligned execution/price/basis state."""

    if side not in (-1, 1):
        return None
    flow = side * finite_number(row["flow_60s"])
    return_bps = side * finite_number(row["ret_60s_bps"])
    basis = side * finite_number(row["basis_change_5m"])
    if not all(math.isfinite(value) for value in (flow, return_bps, basis)):
        return None
    if flow <= 0.0 or return_bps <= 0.0 or basis <= 0.0:
        return None
    return AlignedState(
        side=side,
        flow_60s=flow,
        return_60s_bps=return_bps,
        basis_change_5m_bps=basis,
    )


def objective_boundary(intent: Intent) -> float:
    details = intent.details
    scenario = str(intent.scenario)
    key = {
        BALANCED_PARENT: "boundary_level",
        STRESS_PARENT: "sweep_extreme",
        EXTERNAL_PARENT: "origin",
    }.get(scenario)
    if key is None:
        return float("nan")
    return finite_number(details.get(key))


def on_side(close: float, side: int, boundary: float) -> bool:
    return bool(
        side in (-1, 1)
        and math.isfinite(close)
        and math.isfinite(boundary)
        and side * (close - boundary) > 0.0
    )


def external_reversal_allowed(details: dict[str, Any]) -> bool:
    """A reclaim may fade only a counter-parent or parent-weaker shock.

    A shock aligned with a stronger same-direction 480-minute parent auction is
    an interruption inside ongoing price discovery, not failed discovery.
    """

    shock_side = int(details.get("shock_side", 0))
    parent = finite_number(details.get("pre_shock_parent_480m_return_bps"))
    impact = finite_number(details.get("impact_absolute_return_bps"))
    if shock_side not in (-1, 1):
        return False
    if not all(math.isfinite(value) for value in (parent, impact)):
        return False
    counter_parent = shock_side * parent <= 0.0
    parent_weaker = abs(parent) <= impact
    return counter_parent or parent_weaker


def _open_interest(data: pd.DataFrame, index: int) -> float:
    if not 0 <= index < len(data):
        return float("nan")
    return finite_number(data["metric_sum_open_interest"].iloc[index])


def original_hold_cause(
    data: pd.DataFrame,
    intent: Intent,
    confirmation_index: int,
) -> tuple[bool, dict[str, Any]]:
    scenario = str(intent.scenario)
    signal_index = int(intent.signal_index)
    if scenario == BALANCED_PARENT:
        signal_oi = _open_interest(data, signal_index)
        confirmation_oi = _open_interest(data, confirmation_index)
        # Once the failed breakout has reclaimed the balanced-session boundary,
        # additional breakout inventory must no longer be accumulating.
        passed = (
            math.isfinite(signal_oi)
            and math.isfinite(confirmation_oi)
            and confirmation_oi <= signal_oi
        )
        return passed, {
            "signal_open_interest": signal_oi,
            "confirmation_open_interest": confirmation_oi,
            "breakout_inventory_no_longer_expanding": passed,
        }
    if scenario == STRESS_PARENT:
        # Directionally aligned futures-index basis is already required by the
        # completed confirmation bar. OI may contract through short-covering or
        # expand through fresh inventory; both can support price acceptance.
        return True, {}
    if scenario == EXTERNAL_PARENT:
        passed = external_reversal_allowed(intent.details)
        return passed, {
            "external_reversal_allowed": passed,
            "shock_side": intent.details.get("shock_side"),
            "pre_shock_parent_480m_return_bps": intent.details.get(
                "pre_shock_parent_480m_return_bps"
            ),
            "impact_absolute_return_bps": intent.details.get(
                "impact_absolute_return_bps"
            ),
        }
    return False, {}


def failure_cause(
    data: pd.DataFrame,
    intent: Intent,
    confirmation_index: int,
) -> tuple[bool, dict[str, Any]]:
    scenario = str(intent.scenario)
    signal_index = int(intent.signal_index)
    if scenario == BALANCED_PARENT:
        attack_index = int(intent.details.get("attack_index", -1))
        attack_oi = _open_interest(data, attack_index)
        signal_oi = _open_interest(data, signal_index)
        confirmation_oi = _open_interest(data, confirmation_index)
        # Reacceptance outside the session boundary is continuation only when
        # the newly created breakout inventory remains in the market.
        passed = (
            math.isfinite(attack_oi)
            and math.isfinite(signal_oi)
            and math.isfinite(confirmation_oi)
            and signal_oi > attack_oi
            and confirmation_oi >= attack_oi
        )
        return passed, {
            "attack_open_interest": attack_oi,
            "first_reclaim_open_interest": signal_oi,
            "failure_confirmation_open_interest": confirmation_oi,
            "breakout_inventory_persists": passed,
        }
    if scenario == STRESS_PARENT:
        parent_index = int(intent.details.get("parent_reversal_signal_index", -1))
        parent_oi = _open_interest(data, parent_index)
        confirmation_oi = _open_interest(data, confirmation_index)
        # A failed stress acceptance is a deleveraging reversal only when OI did
        # not expand from the original rejected-auction state.
        passed = (
            math.isfinite(parent_oi)
            and math.isfinite(confirmation_oi)
            and confirmation_oi <= parent_oi
        )
        return passed, {
            "parent_reversal_open_interest": parent_oi,
            "failure_confirmation_open_interest": confirmation_oi,
            "deleveraging_not_new_inventory": passed,
        }
    if scenario == EXTERNAL_PARENT:
        allowed = external_reversal_allowed(intent.details)
        passed = not allowed
        return passed, {
            "strong_parent_aligned_impact": passed,
            "shock_side": intent.details.get("shock_side"),
            "pre_shock_parent_480m_return_bps": intent.details.get(
                "pre_shock_parent_480m_return_bps"
            ),
            "impact_absolute_return_bps": intent.details.get(
                "impact_absolute_return_bps"
            ),
        }
    return False, {}


def _causal_stop(
    data: pd.DataFrame,
    start_index: int,
    confirmation_index: int,
    side: int,
    stop_buffer_atr: float,
    original_stop: float | None,
) -> float:
    segment = data.iloc[start_index : confirmation_index + 1]
    if segment.empty or side not in (-1, 1):
        return float("nan")
    atr = finite_number(data["atr"].iloc[confirmation_index])
    if not math.isfinite(atr) or atr <= 0.0:
        return float("nan")
    extreme = finite_number(
        segment["low"].min() if side > 0 else segment["high"].max()
    )
    stop = extreme - side * float(stop_buffer_atr) * atr
    if original_stop is not None and math.isfinite(original_stop):
        stop = min(stop, original_stop) if side > 0 else max(stop, original_stop)
    close = finite_number(data["close"].iloc[confirmation_index])
    if not math.isfinite(close) or side * (close - stop) <= 0.0:
        return float("nan")
    return stop


def _resolved_intent(
    data: pd.DataFrame,
    parent: Intent,
    confirmation_index: int,
    side: int,
    scenario: str,
    branch: str,
    boundary: float,
    state: AlignedState,
    cause_details: dict[str, Any],
    impact_parameters: Any,
) -> Intent | None:
    original_stop = (
        finite_number(parent.stop_level) if side == int(parent.side) else None
    )
    stop = _causal_stop(
        data,
        int(parent.signal_index),
        confirmation_index,
        side,
        float(impact_parameters.stop_buffer_atr),
        original_stop,
    )
    if not math.isfinite(stop):
        return None
    details = {
        **parent.details,
        **cause_details,
        "parent_scenario": str(parent.scenario),
        "parent_signal_index": int(parent.signal_index),
        "parent_stop_level": finite_number(parent.stop_level),
        "resolution_boundary": boundary,
        "resolution_branch": branch,
        "resolution_confirmation_index": confirmation_index,
        "resolution_delay_bars": confirmation_index - int(parent.signal_index),
        "resolution_directional_flow_60s": state.flow_60s,
        "resolution_directional_return_60s_bps": state.return_60s_bps,
        "resolution_directional_basis_change_5m_bps": (
            state.basis_change_5m_bps
        ),
        "compiler": "candidate-04-two-stage-auction-resolution",
    }
    return Intent(
        scenario=scenario,
        side=side,
        signal_index=confirmation_index,
        entry_index=confirmation_index + 1,
        stop_level=stop,
        event_indices=tuple(
            [*tuple(int(value) for value in parent.event_indices), confirmation_index]
        ),
        details=details,
    )


def resolve_parent_intent(
    data: pd.DataFrame,
    parent: Intent,
    evaluation_end: pd.Timestamp,
    impact_parameters: Any,
) -> tuple[Intent | None, str]:
    boundary = objective_boundary(parent)
    if not math.isfinite(boundary):
        return None, "invalid_boundary"
    parent_side = int(parent.side)
    if parent_side not in (-1, 1):
        return None, "invalid_side"
    start = int(parent.signal_index) + 1
    upper = min(int(parent.signal_index) + MAX_RESOLUTION_BARS, len(data) - 2)
    for index in range(start, upper + 1):
        if data.index[index] > evaluation_end:
            break
        row = data.iloc[index]
        close = finite_number(row["close"])
        if not math.isfinite(close):
            continue
        if on_side(close, parent_side, boundary):
            state = aligned_state(row, parent_side)
            if state is None:
                continue
            cause_passed, cause_details = original_hold_cause(
                data,
                parent,
                index,
            )
            if not cause_passed:
                continue
            resolved = _resolved_intent(
                data,
                parent,
                index,
                parent_side,
                HOLD_SCENARIOS[str(parent.scenario)],
                "ORIGINAL_SIDE_HOLD",
                boundary,
                state,
                cause_details,
                impact_parameters,
            )
            if resolved is not None:
                return resolved, "original_hold"
        else:
            failure_side = -parent_side
            state = aligned_state(row, failure_side)
            if state is None:
                continue
            cause_passed, cause_details = failure_cause(
                data,
                parent,
                index,
            )
            if not cause_passed:
                continue
            resolved = _resolved_intent(
                data,
                parent,
                index,
                failure_side,
                FAILURE_SCENARIOS[str(parent.scenario)],
                "OPPOSITE_SIDE_REACCEPTANCE",
                boundary,
                state,
                cause_details,
                impact_parameters,
            )
            if resolved is not None:
                return resolved, "failure_route"
    return None, "unresolved"


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    parent_intents, parent_summary = v29.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    accepted: list[Intent] = []
    counts: dict[str, Any] = {
        "parent_signals_routed": 0,
        "parent_signals_unchanged": 0,
        "resolved_original_hold": 0,
        "resolved_failure_route": 0,
        "unresolved": 0,
        "invalid_boundary": 0,
        "invalid_side": 0,
        "by_parent": {},
    }
    for parent in parent_intents:
        scenario = str(parent.scenario)
        if scenario not in ROUTED_PARENTS:
            accepted.append(parent)
            counts["parent_signals_unchanged"] += 1
            continue
        counts["parent_signals_routed"] += 1
        by_parent = counts["by_parent"].setdefault(
            scenario,
            {"routed": 0, "original_hold": 0, "failure_route": 0, "unresolved": 0},
        )
        by_parent["routed"] += 1
        resolved, outcome = resolve_parent_intent(
            data,
            parent,
            evaluation_end,
            impact_parameters,
        )
        if resolved is not None:
            accepted.append(resolved)
        if outcome == "original_hold":
            counts["resolved_original_hold"] += 1
            by_parent["original_hold"] += 1
        elif outcome == "failure_route":
            counts["resolved_failure_route"] += 1
            by_parent["failure_route"] += 1
        else:
            counts[outcome] = int(counts.get(outcome, 0)) + 1
            by_parent["unresolved"] += 1

    priority = {
        **{scenario: 0 for scenario in FAILURE_SCENARIOS.values()},
        **{scenario: 1 for scenario in HOLD_SCENARIOS.values()},
    }
    accepted.sort(
        key=lambda item: (
            int(item.signal_index),
            priority.get(str(item.scenario), 2),
        )
    )
    unique: list[Intent] = []
    seen_indices: set[int] = set()
    duplicate_resolution_bars = 0
    for intent in accepted:
        index = int(intent.signal_index)
        if index in seen_indices:
            duplicate_resolution_bars += 1
            continue
        seen_indices.add(index)
        unique.append(intent)
    counts["duplicate_resolution_bars"] = duplicate_resolution_bars

    router_contract = dict(parent_summary.get("router_contract", {}))
    router_contract["two_stage_resolution"] = {
        "routed_parents": sorted(ROUTED_PARENTS),
        "boundary_resolution": (
            "a later completed bar must remain on the intended side or "
            "reaccept the opposite side with flow, return and futures-index "
            "basis aligned"
        ),
        "balanced_failure_cause": "new breakout OI remains present",
        "stress_failure_cause": "OI does not expand from the rejected-auction parent",
        "external_failure_cause": "shock is aligned with a stronger parent auction",
        "maximum_resolution_bars": MAX_RESOLUTION_BARS,
    }
    return unique, {
        **parent_summary,
        "candidate": "candidate-04-v30-two-stage-auction-resolution",
        "compiler": "candidate-04-v30-two-stage-auction-resolution",
        "raw_routed_signals": len(unique),
        "unique_signal_bars": len(unique),
        "two_stage_route_counts": counts,
        "router_contract": router_contract,
        "changes_from_v29": {
            "changed_entry_state": 1,
            "change": (
                "three first-reclaim parent scenarios require a second completed "
                "boundary-resolution state; all other V29 mechanisms remain unchanged"
            ),
            "execution": "NautilusTrader BacktestNode",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
