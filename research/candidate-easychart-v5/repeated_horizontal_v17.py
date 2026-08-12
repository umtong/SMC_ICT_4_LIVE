"""Repeated-defense horizontal context for the canonical EasyChart policy.

The source's Fakeout/Trap examples trade major, obvious support or resistance,
not every isolated local pivot.  A machine-visible horizontal context therefore
requires two distinct confirmed wick pivots whose wick-to-body rejection areas
overlap.  The exact intersection becomes the boundary.  Individual pivots stay
available as pre-existing objectives, but no longer create trades by themselves.

This changes only horizontal context recognition.  Trend lines, channels,
entries, stops, targets, three-percent risk, one-R eligibility and the full
single-exit contract remain unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, Pivot, StructureFamily, StructureZone
from domain import Candle, Side
from easychart_zones import ZoneSide
from scenario_channel_extension_v16 import (
    ChannelExtensionTargetScenarioEngine,
    MicroChannelExtensionBundleV16,
)
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


REPEATED_HORIZONTAL_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "HORIZONTAL_CONTEXT_REQUIRES_TWO_DISTINCT_CONFIRMED_OVERLAPPING_REJECTION_AREAS"
)
if REPEATED_HORIZONTAL_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (REPEATED_HORIZONTAL_RULE,)


class RepeatedDefenseStructureBook(NearestAnyPivotStructureBook):
    """Use repeated wick-to-body defense zones as horizontal trade context."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.horizontal_structures: list[StructureZone] = []
        self._horizontal_by_source: dict[str, StructureZone] = {}
        self._active_horizontal: dict[str, StructureZone] = {}
        self._pivot_rejection_areas: dict[str, tuple[float, float]] = {}

    def _rejection_area(self, pivot: Pivot) -> tuple[float, float]:
        bar = self.bars[pivot.index]
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        return (bar.low, body_low) if pivot.side == "LOW" else (body_high, bar.high)

    def _area_for(self, pivot: Pivot) -> tuple[float, float]:
        area = self._pivot_rejection_areas.get(pivot.pivot_id)
        if area is None:
            area = self._rejection_area(pivot)
            self._pivot_rejection_areas[pivot.pivot_id] = area
        return area

    def _held_between(
        self,
        side: str,
        shared_lower: float,
        shared_upper: float,
        first: Pivot,
        second: Pivot,
    ) -> bool:
        """Reject a level already accepted through before the second defense."""
        intervening = self.bars[first.index + 1 : second.index]
        if side == "LOW":
            return all(bar.close >= shared_lower for bar in intervening)
        return all(bar.close <= shared_upper for bar in intervening)

    def _candidate_prior_pivots(
        self,
        pivot: Pivot,
    ) -> list[tuple[Pivot, float, float, float]]:
        lower, upper = self._area_for(pivot)
        candidates: list[tuple[Pivot, float, float, float]] = []
        for prior in self.pivots:
            if prior.pivot_id == pivot.pivot_id or prior.side != pivot.side:
                continue
            if prior.index >= pivot.index:
                continue
            prior_lower, prior_upper = self._area_for(prior)
            shared_lower = max(lower, prior_lower)
            shared_upper = min(upper, prior_upper)
            if shared_lower >= shared_upper:
                continue
            if not self._held_between(
                pivot.side,
                shared_lower,
                shared_upper,
                prior,
                pivot,
            ):
                continue
            candidates.append(
                (prior, shared_lower, shared_upper, shared_upper - shared_lower),
            )
        return candidates

    def _create_horizontal_structure(self, pivot: Pivot) -> StructureZone | None:
        self._area_for(pivot)
        candidates = self._candidate_prior_pivots(pivot)
        if not candidates:
            return None
        prior, lower, upper, _ = max(
            candidates,
            key=lambda item: (
                item[3],
                min(item[0].span, pivot.span),
                pivot.index - item[0].index,
                item[0].pivot_id,
            ),
        )
        kind = (
            ObjectKind.HORIZONTAL_SUPPORT
            if pivot.side == "LOW"
            else ObjectKind.HORIZONTAL_RESISTANCE
        )
        side = ZoneSide.SUPPORT if pivot.side == "LOW" else ZoneSide.RESISTANCE
        source_id = (
            f"{self.symbol}:{self.timeframe_minutes}m:REPEATED_{kind.value}:"
            f"{prior.pivot_id}|{pivot.pivot_id}"
        )
        if source_id in self._horizontal_by_source:
            return None
        invalidation = (
            lower - self.tick_size
            if side is ZoneSide.SUPPORT
            else upper + self.tick_size
        )
        zone = StructureZone(
            zone_id=f"{source_id}:SNAP:{pivot.observed_time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=lower if side is ZoneSide.SUPPORT else upper,
            formed_index=pivot.index,
            formed_time_ns=pivot.event_time_ns,
            observed_time_ns=max(prior.observed_time_ns, pivot.observed_time_ns),
            formation_indices=(prior.index, pivot.index),
            strength_ratio=min(prior.strength_ratio, pivot.strength_ratio),
            source_structure_id=source_id,
            source_pivot_span=max(prior.span, pivot.span),
        )
        self.horizontal_structures.append(zone)
        self._horizontal_by_source[source_id] = zone
        self._active_horizontal[source_id] = zone
        self._inc(f"repeated_{kind.value.lower()}_confirmed")
        return zone

    def on_bar(self, bar: Candle):  # type: ignore[no-untyped-def]
        pivots, lines, channels = super().on_bar(bar)
        for pivot in pivots:
            self._create_horizontal_structure(pivot)
        return pivots, lines, channels

    def observe_price(self, bar: Candle) -> None:
        # Preserve individual-pivot lifecycle for objective selection.
        super().observe_price(bar)
        for source_id, zone in list(self._active_horizontal.items()):
            if bar.ts_close_ns <= zone.observed_time_ns:
                continue
            touched = bar.low <= zone.upper and bar.high >= zone.lower
            if not touched:
                continue
            zone.consumed = True
            zone.consumed_time_ns = bar.ts_close_ns
            self._active_horizontal.pop(source_id, None)
            self._inc("repeated_horizontal_first_interaction")

    def _repeated_snapshot(self, zone: StructureZone, time_ns: int) -> StructureZone:
        return replace(
            zone,
            zone_id=f"{zone.source_structure_id}:SNAP:{time_ns}",
            consumed=zone.consumed and (zone.consumed_time_ns or 0) < time_ns,
        )

    def boundaries_at(self, time_ns: int) -> list[StructureZone]:
        # Base horizontal snapshots are single pivots; retain only diagonal
        # structures from the base book, then add repeated-defense horizontals.
        output = [
            zone
            for zone in super().boundaries_at(time_ns)
            if zone.family is not StructureFamily.HORIZONTAL
        ]
        output.extend(
            self._repeated_snapshot(zone, time_ns)
            for zone in self._active_horizontal.values()
            if zone.observed_time_ns < time_ns
        )
        return output

    def snapshot_for(self, zone: StructureZone, time_ns: int) -> StructureZone:
        repeated = self._horizontal_by_source.get(zone.source_structure_id)
        if repeated is not None:
            return self._repeated_snapshot(repeated, time_ns)
        return super().snapshot_for(zone, time_ns)


class RepeatedHorizontalScenarioEngine(ChannelExtensionTargetScenarioEngine):
    """Channel-aware engine whose horizontal contexts require two defenses."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = RepeatedDefenseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class MicroRepeatedHorizontalBundleV17(MicroChannelExtensionBundleV16):
    """Micro candidate with source-level horizontal structure semantics."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = RepeatedHorizontalScenarioEngine(
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
            "name": "TWO_CONFIRMED_OVERLAPPING_REJECTION_AREAS",
            "rule_provenance": REPEATED_HORIZONTAL_RULE,
        }
        return output
