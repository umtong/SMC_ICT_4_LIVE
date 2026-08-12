"""First still-active opposing structural objective selection.

The source material repeatedly says to exit at the first opposing structure. A
historical pivot whose liquidity was already traded through is no longer an
opposing objective merely because its old price lies between entry and a far
session boundary. Earlier v13 code selected such stale micro pivots and then
rejected the entire trade when the setup bar had already crossed them.

The corrected hierarchy is causal and explicit:

* only pivots confirmed before the setup may be considered;
* retire pivots consumed before the setup and pivots crossed by the setup bar;
* choose the nearest remaining opposing pivot between entry and the far cap;
* if that active objective offers less than the user-fixed 1.0R, reject rather
  than skipping it for a farther target;
* if no active internal objective exists, retain the already-declared far
  structural cap, whose own setup-bar consumption is checked upstream.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from domain_v3 import ArmedSetup, Side
from market_v4 import StructuralPivot


PivotKey = tuple[str, int, int, float]


def pivot_key(pivot: StructuralPivot) -> PivotKey:
    return (
        pivot.side,
        int(pivot.event_time_ns),
        int(pivot.observed_time_ns),
        float(pivot.level),
    )


@dataclass(frozen=True, slots=True)
class FirstObjectiveDecision:
    setup: ArmedSetup | None
    reason: str
    pivot: StructuralPivot | None = None
    candidates: tuple[StructuralPivot, ...] = ()
    excluded_consumed: tuple[StructuralPivot, ...] = ()


def select_first_directional_objective(
    *,
    setup: ArmedSetup,
    pivots: Iterable[StructuralPivot],
    setup_bar_high: float,
    setup_bar_low: float,
    timeframe_minutes: int,
    consumed_pivot_keys: set[PivotKey] | None = None,
) -> FirstObjectiveDecision:
    """Select the nearest still-active, already-observed opposing swing."""
    consumed = consumed_pivot_keys or set()
    eligible = [
        pivot
        for pivot in pivots
        if pivot.observed_time_ns < setup.observed_time_ns
    ]
    if setup.side is Side.LONG:
        candidates = [
            pivot
            for pivot in eligible
            if pivot.side == "HIGH" and setup.entry < pivot.level <= setup.initial_target
        ]
        excluded = [
            pivot
            for pivot in candidates
            if pivot_key(pivot) in consumed or setup_bar_high >= pivot.level
        ]
        active = [pivot for pivot in candidates if pivot not in excluded]
        chosen = min(active, default=None, key=lambda pivot: pivot.level)
    else:
        candidates = [
            pivot
            for pivot in eligible
            if pivot.side == "LOW" and setup.initial_target <= pivot.level < setup.entry
        ]
        excluded = [
            pivot
            for pivot in candidates
            if pivot_key(pivot) in consumed or setup_bar_low <= pivot.level
        ]
        active = [pivot for pivot in candidates if pivot not in excluded]
        chosen = max(active, default=None, key=lambda pivot: pivot.level)

    candidate_tuple = tuple(
        sorted(candidates, key=lambda pivot: (pivot.level, pivot.event_time_ns)),
    )
    excluded_tuple = tuple(
        sorted(excluded, key=lambda pivot: (pivot.level, pivot.event_time_ns)),
    )
    if chosen is None:
        return FirstObjectiveDecision(
            setup,
            "NO_ACTIVE_INTERNAL_OBJECTIVE_USE_FAR_CAP",
            candidates=candidate_tuple,
            excluded_consumed=excluded_tuple,
        )

    candidate = replace(
        setup,
        family=f"{setup.family}_FIRST_ACTIVE_DC_OBJECTIVE_{timeframe_minutes}M",
        causal_event_id=f"{setup.causal_event_id}:FIRST_ACTIVE_DC:{chosen.event_time_ns}",
        initial_target=chosen.level,
        fixed_target_id=f"FIRST_ACTIVE_DC_PIVOT:{chosen.side}:{chosen.event_time_ns}",
        context_bias=f"{setup.context_bias}|FIRST_ACTIVE_DC_OBJECTIVE={chosen.level}",
    )
    if candidate.executable(
        candidate.initial_target,
        target_id=candidate.fixed_target_id,
        min_gross_rr=1.0,
    ) is None:
        return FirstObjectiveDecision(
            None,
            "FIRST_ACTIVE_OBJECTIVE_RR_LT_1",
            chosen,
            candidate_tuple,
            excluded_tuple,
        )
    return FirstObjectiveDecision(
        candidate,
        "FIRST_ACTIVE_OBJECTIVE_SELECTED",
        chosen,
        candidate_tuple,
        excluded_tuple,
    )


__all__ = [
    "FirstObjectiveDecision",
    "PivotKey",
    "pivot_key",
    "select_first_directional_objective",
]
