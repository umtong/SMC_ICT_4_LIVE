#!/usr/bin/env python3
"""Compile completed-session inventory-acceptance continuations.

This independent scenario addresses the failure of immediate session fades. A
completed 8-hour boundary is not presumed to reverse. Continuation becomes
tradable only after:

1. the first meaningful penetration closes outside the previous session range;
2. executed flow and price displacement align with the break;
3. open interest increases and the futures-index basis changes in the break
   direction, indicating new directional inventory rather than pure liquidation;
4. a later completed bar remains outside with aligned flow, price, OI and basis;
5. the confirmation displacement is no larger than the initial break, excluding
   a second climactic impact event.

The broken session boundary is the causal invalidation. The compiler emits no
fills or PnL; NautilusTrader owns target selection, orders, costs, risk and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
from session_liquidity_resiliency_compiler import session_start


Intent = v22.Intent
SCENARIO = "COMPLETED_SESSION_INVENTORY_ACCEPTANCE_CONTINUATION"
CONFIRMATION_BARS = 3


@dataclass(frozen=True, slots=True)
class SessionBoundary:
    session_start: pd.Timestamp
    high: float
    low: float


def _session_boundaries(data: pd.DataFrame) -> dict[pd.Timestamp, SessionBoundary]:
    starts = pd.Series([session_start(value) for value in data.index], index=data.index)
    groups: list[tuple[pd.Timestamp, pd.DataFrame]] = list(data.groupby(starts, sort=True))
    result: dict[pd.Timestamp, SessionBoundary] = {}
    for index in range(1, len(groups)):
        current_start, _ = groups[index]
        previous_start, previous = groups[index - 1]
        result[current_start] = SessionBoundary(
            session_start=previous_start,
            high=float(previous["high"].max()),
            low=float(previous["low"].min()),
        )
    return result


def outside_boundary(close: float, side: int, level: float) -> bool:
    return close > level if side > 0 else close < level


def non_climactic_hold(
    confirmation_return_bps: float,
    break_return_bps: float,
) -> bool:
    if not all(
        math.isfinite(value)
        for value in (confirmation_return_bps, break_return_bps)
    ):
        return False
    if break_return_bps <= 0.0:
        return False
    return 0.0 < confirmation_return_bps <= break_return_bps


def _inventory_alignment(row: pd.Series, side: int) -> tuple[bool, dict[str, float]]:
    flow = side * float(row["flow_60s"])
    directional_return = side * float(row["ret_60s_bps"])
    oi_change = float(row["metric_oi_change_15m"])
    basis_change = side * float(row["basis_change_5m"])
    finite = all(
        math.isfinite(value)
        for value in (flow, directional_return, oi_change, basis_change)
    )
    passed = (
        finite
        and flow > 0.0
        and directional_return > 0.0
        and oi_change > 0.0
        and basis_change > 0.0
    )
    return passed, {
        "directional_flow_60s": flow,
        "directional_return_60s_bps": directional_return,
        "open_interest_change_15m": oi_change,
        "directional_basis_change_5m_bps": basis_change,
    }


def detect_session_inventory_acceptance_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    boundaries = _session_boundaries(data)
    starts = [session_start(value) for value in data.index]
    consumed: set[tuple[pd.Timestamp, int]] = set()
    intents: list[Intent] = []
    counts = {
        "first_boundary_penetrations": 0,
        "ambiguous_both_boundaries": 0,
        "break_closed_inside": 0,
        "break_without_inventory_alignment": 0,
        "reclaimed_before_confirmation": 0,
        "no_persistent_inventory_confirmation": 0,
        "confirmed_acceptance": 0,
    }

    for index, timestamp in enumerate(data.index):
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        current_start = starts[index]
        boundary = boundaries.get(current_start)
        if boundary is None:
            continue
        row = data.iloc[index]
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            continue

        high_key = (current_start, 1)
        low_key = (current_start, -1)
        high_penetration = (float(row["high"]) - boundary.high) / atr
        low_penetration = (boundary.low - float(row["low"])) / atr
        high_taken = (
            high_key not in consumed
            and high_penetration >= float(config.sweep_min_atr)
        )
        low_taken = (
            low_key not in consumed
            and low_penetration >= float(config.sweep_min_atr)
        )
        if not (high_taken or low_taken):
            continue
        if high_taken:
            consumed.add(high_key)
        if low_taken:
            consumed.add(low_key)
        counts["first_boundary_penetrations"] += int(high_taken) + int(low_taken)
        if high_taken and low_taken:
            counts["ambiguous_both_boundaries"] += 1
            continue

        side = 1 if high_taken else -1
        level = boundary.high if high_taken else boundary.low
        penetration = high_penetration if high_taken else low_penetration
        if not outside_boundary(float(row["close"]), side, level):
            counts["break_closed_inside"] += 1
            continue
        break_aligned, break_state = _inventory_alignment(row, side)
        if not break_aligned:
            counts["break_without_inventory_alignment"] += 1
            continue

        break_return = float(break_state["directional_return_60s_bps"])
        confirmation_index: int | None = None
        confirmation_state: dict[str, float] | None = None
        upper = min(index + CONFIRMATION_BARS, len(data) - 2)
        reclaimed = False
        for candidate_index in range(index + 1, upper + 1):
            candidate = data.iloc[candidate_index]
            if not outside_boundary(float(candidate["close"]), side, level):
                reclaimed = True
                break
            aligned, state = _inventory_alignment(candidate, side)
            if not aligned:
                continue
            if not non_climactic_hold(
                float(state["directional_return_60s_bps"]),
                break_return,
            ):
                continue
            confirmation_index = candidate_index
            confirmation_state = state
            break

        if reclaimed:
            counts["reclaimed_before_confirmation"] += 1
            continue
        if confirmation_index is None or confirmation_state is None:
            counts["no_persistent_inventory_confirmation"] += 1
            continue
        if data.index[confirmation_index] > evaluation_end:
            continue

        stop_level = level - side * float(impact_parameters.stop_buffer_atr) * atr
        details = {
            "liquidity_source": "PREVIOUS_COMPLETED_8H_SESSION_BOUNDARY",
            "previous_session_start": boundary.session_start.isoformat(),
            "current_session_start": current_start.isoformat(),
            "side": side,
            "boundary_level": level,
            "penetration_atr": penetration,
            "break_index": index,
            "confirmation_index": confirmation_index,
            "confirmation_delay_bars": confirmation_index - index,
            "break_state": break_state,
            "confirmation_state": confirmation_state,
            "confirmation_to_break_return_ratio": (
                float(confirmation_state["directional_return_60s_bps"])
                / break_return
            ),
            "compiler": "candidate-04-session-inventory-acceptance-v1",
        }
        intents.append(
            Intent(
                scenario=SCENARIO,
                side=side,
                signal_index=confirmation_index,
                entry_index=confirmation_index + 1,
                stop_level=stop_level,
                event_indices=(index, confirmation_index),
                details=details,
            ),
        )
        counts["confirmed_acceptance"] += 1

    intents.sort(key=lambda item: int(item.signal_index))
    unique: list[Intent] = []
    seen: set[int] = set()
    for intent in intents:
        index = int(intent.signal_index)
        if index in seen:
            continue
        seen.add(index)
        unique.append(intent)
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
    intents, counts = detect_session_inventory_acceptance_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-session-inventory-acceptance-v1",
        "compiler": "candidate-04-session-inventory-acceptance-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "liquidity": "previous completed UTC 8-hour session high or low",
            "break": "first meaningful penetration closes outside",
            "inventory": (
                "aligned executed flow and return, positive OI change, and "
                "basis change in the break direction"
            ),
            "persistence": "later completed bar holds outside with the same inventory state",
            "non_climactic": "confirmation directional return <= break directional return",
            "invalidation": "close back inside the completed-session boundary",
            "risk_and_execution": "unchanged NautilusTrader path",
        },
        "constants": {
            "confirmation_bars": CONFIRMATION_BARS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
