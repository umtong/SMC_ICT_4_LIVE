"""First opposing structural objective selection for candidate-easychart v13.

The source material repeatedly says to exit at the first opposing structure. A
human naturally sees nearer confirmed highs/lows before the far edge of an
entire session range; earlier session screens did not. This module makes that
objective hierarchy causal and explicit:

* only pivots confirmed before the setup may be considered;
* choose the nearest opposing pivot between entry and the far structural cap;
* if it was already consumed on the setup bar, reject;
* if it offers less than the user-fixed 1.0R, reject rather than skipping it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from domain_v3 import ArmedSetup, Side
from market_v4 import StructuralPivot


@dataclass(frozen=True, slots=True)
class FirstObjectiveDecision:
    setup: ArmedSetup | None
    reason: str
    pivot: StructuralPivot | None = None


def select_first_directional_objective(
    *,
    setup: ArmedSetup,
    pivots: Iterable[StructuralPivot],
    setup_bar_high: float,
    setup_bar_low: float,
    timeframe_minutes: int,
) -> FirstObjectiveDecision:
    """Select the nearest already-observed opposing swing objective."""
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
        chosen = min(candidates, default=None, key=lambda pivot: pivot.level)
        if chosen is not None and setup_bar_high >= chosen.level:
            return FirstObjectiveDecision(None, "OBJECTIVE_CONSUMED_ON_SETUP_BAR", chosen)
    else:
        candidates = [
            pivot
            for pivot in eligible
            if pivot.side == "LOW" and setup.initial_target <= pivot.level < setup.entry
        ]
        chosen = max(candidates, default=None, key=lambda pivot: pivot.level)
        if chosen is not None and setup_bar_low <= chosen.level:
            return FirstObjectiveDecision(None, "OBJECTIVE_CONSUMED_ON_SETUP_BAR", chosen)

    if chosen is None:
        return FirstObjectiveDecision(setup, "NO_INTERNAL_OBJECTIVE_USE_FAR_CAP")

    candidate = replace(
        setup,
        family=f"{setup.family}_FIRST_DC_OBJECTIVE_{timeframe_minutes}M",
        causal_event_id=f"{setup.causal_event_id}:FIRST_DC:{chosen.event_time_ns}",
        initial_target=chosen.level,
        fixed_target_id=f"FIRST_DC_PIVOT:{chosen.side}:{chosen.event_time_ns}",
        context_bias=f"{setup.context_bias}|FIRST_DC_OBJECTIVE={chosen.level}",
    )
    if candidate.executable(
        candidate.initial_target,
        target_id=candidate.fixed_target_id,
        min_gross_rr=1.0,
    ) is None:
        return FirstObjectiveDecision(None, "FIRST_OBJECTIVE_RR_LT_1", chosen)
    return FirstObjectiveDecision(candidate, "FIRST_OBJECTIVE_SELECTED", chosen)


__all__ = ["FirstObjectiveDecision", "select_first_directional_objective"]
