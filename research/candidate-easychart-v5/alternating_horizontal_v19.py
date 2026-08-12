"""Alternating-swing horizontal structure for the integrated EasyChart policy.

A double top/bottom or contraction level is an alternating swing sequence, not
an arbitrary pair of any two historic pivots at a similar price. For each new
physical pivot this module considers only the immediately preceding physical
pivot on the same side and requires at least one opposite pivot between them.
Their wick-to-body rejection areas must still overlap exactly and the shared
area must remain unaccepted through the second pivot's confirmation bars.

This corrects the overproduction discovered in v17 while retaining individual
pivots as objectives. No execution, risk, management, time or daily rule is
changed.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import Pivot
from mainline_origin_stop_v18 import (
    MainLineOriginStopScenarioEngine,
    MicroMainLineOriginStopBundleV18,
)
from repeated_horizontal_v17 import RepeatedDefenseStructureBook
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


ALTERNATING_HORIZONTAL_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "HORIZONTAL_CONTRACTION_USES_ADJACENT_SAME_SIDE_SWINGS_WITH_AN_OPPOSITE_SWING_BETWEEN"
)
if ALTERNATING_HORIZONTAL_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (ALTERNATING_HORIZONTAL_RULE,)


class AlternatingRepeatedDefenseStructureBook(RepeatedDefenseStructureBook):
    """Build at most one horizontal candidate from each physical swing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._processed_horizontal_physical_swings: set[tuple[str, int]] = set()

    def _held_between(
        self,
        side: str,
        shared_lower: float,
        shared_upper: float,
        first: Pivot,
        second: Pivot,
    ) -> bool:
        # A level must still be valid when the second pivot becomes observable,
        # not merely on the historical pivot candle.
        through_confirmation = self.bars[first.index + 1 : second.observed_index + 1]
        if side == "LOW":
            return all(bar.close >= shared_lower for bar in through_confirmation)
        return all(bar.close <= shared_upper for bar in through_confirmation)

    def _physical_pivot_at(self, side: str, index: int) -> Pivot:
        candidates = [
            item for item in self.pivots if item.side == side and item.index == index
        ]
        if not candidates:
            raise RuntimeError("physical pivot disappeared")
        return max(candidates, key=lambda item: (item.span, item.strength_ratio, item.pivot_id))

    def _candidate_prior_pivots(
        self,
        pivot: Pivot,
    ) -> list[tuple[Pivot, float, float, float]]:
        prior_indices = {
            item.index
            for item in self.pivots
            if item.side == pivot.side and item.index < pivot.index
        }
        if not prior_indices:
            return []
        prior = self._physical_pivot_at(pivot.side, max(prior_indices))
        opposite_side = "HIGH" if pivot.side == "LOW" else "LOW"
        has_opposite = any(
            item.side == opposite_side
            and prior.index < item.index < pivot.index
            and item.observed_time_ns <= pivot.observed_time_ns
            for item in self.pivots
        )
        if not has_opposite:
            return []

        lower, upper = self._area_for(pivot)
        prior_lower, prior_upper = self._area_for(prior)
        shared_lower = max(lower, prior_lower)
        shared_upper = min(upper, prior_upper)
        if shared_lower >= shared_upper:
            return []
        if not self._held_between(
            pivot.side,
            shared_lower,
            shared_upper,
            prior,
            pivot,
        ):
            return []
        return [(prior, shared_lower, shared_upper, shared_upper - shared_lower)]

    def on_bar(self, bar):  # type: ignore[no-untyped-def]
        # Bypass the v17 all-history pairing loop. Preserve the same causal
        # pivot, line, channel and lifecycle implementation underneath it.
        pivots, lines, channels = NearestAnyPivotStructureBook.on_bar(self, bar)
        earliest_by_physical_swing: dict[tuple[str, int], Pivot] = {}
        for pivot in pivots:
            key = (pivot.side, pivot.index)
            if key in self._processed_horizontal_physical_swings:
                continue
            current = earliest_by_physical_swing.get(key)
            if current is None or pivot.observed_time_ns < current.observed_time_ns:
                earliest_by_physical_swing[key] = pivot
        for key, pivot in earliest_by_physical_swing.items():
            self._processed_horizontal_physical_swings.add(key)
            self._create_horizontal_structure(pivot)
        return pivots, lines, channels


class AlternatingHorizontalScenarioEngine(MainLineOriginStopScenarioEngine):
    """Full integrated engine with alternating horizontal context."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = AlternatingRepeatedDefenseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class MicroAlternatingHorizontalBundleV19(MicroMainLineOriginStopBundleV18):
    """Integrated micro policy with non-combinatorial horizontal structures."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = AlternatingHorizontalScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["horizontal_context_policy"] = {
            "name": "ADJACENT_SAME_SIDE_SWINGS_WITH_OPPOSITE_SWING_AND_OVERLAP",
            "rule_provenance": ALTERNATING_HORIZONTAL_RULE,
        }
        return output
