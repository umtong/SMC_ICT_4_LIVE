"""Source-faithful horizontal structures for EasyChart v3.

A generic local pivot is not automatically the major support/resistance line
used by EasyChart Fakeout/Trap examples.  This module forms a machine-auditable
horizontal structure only after two distinct confirmed wick pivots have
*overlapping rejection areas*:

support pivot rejection area = [wick low, lower body edge]
resistance pivot rejection area = [upper body edge, wick high]

The exact intersection becomes the shared level.  This translates a human's
"the same price area was defended twice" without an arbitrary percentage or
ATR tolerance.  The second pivot must be confirmed before the structure exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from domain import Candle, Side
from easychart_mtf_scenario import MTFTradePlan
from easychart_zones import PriceZone, ZoneSide
from liquidity import CausalLiquidityDetector, ObjectiveKind, ObjectiveZone
from scenario_bundle_v3 import (
    HorizontalState,
    HorizontalSweepScenarioEngine,
    ResearchScenarioBundle,
)


@dataclass(slots=True)
class HorizontalStructureZone:
    zone_id: str
    kind: ObjectiveKind
    side: ZoneSide
    timeframe_minutes: int
    lower: float
    upper: float
    invalidation: float
    impulse_extreme: float
    formed_index: int
    formed_time_ns: int
    observed_time_ns: int
    formation_indices: tuple[int, ...]
    strength_ratio: float
    pivot_span: int
    first_pivot_id: str
    second_pivot_id: str
    touch_count: int = 2
    consumed: bool = False
    consumed_time_ns: int | None = None

    @property
    def active(self) -> bool:
        return not self.consumed


class HorizontalStructureDetector:
    """Confirmed two-touch support/resistance with causal lifecycle."""

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        tick_size: float,
    ) -> None:
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.tick_size = tick_size
        self.pivots = CausalLiquidityDetector(
            symbol,
            timeframe_minutes,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.zones: list[HorizontalStructureZone] = []
        self._active: dict[str, HorizontalStructureZone] = {}
        self._rejection_areas: dict[str, tuple[float, float]] = {}
        self.diagnostics: dict[str, int] = {}
        self._last_observed_price_time_ns = -1

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _rejection_area(self, pivot: ObjectiveZone) -> tuple[float, float]:
        candle = self.pivots.bars[pivot.formed_index]
        body_low = min(candle.open, candle.close)
        body_high = max(candle.open, candle.close)
        if pivot.side is ZoneSide.SUPPORT:
            return candle.low, body_low
        return body_high, candle.high

    def observe_price(self, bar: Candle) -> None:
        if bar.ts_close_ns < self._last_observed_price_time_ns:
            raise ValueError("price observations must be nondecreasing")
        self._last_observed_price_time_ns = bar.ts_close_ns
        self.pivots.observe_price(bar)
        for zone_id, zone in list(self._active.items()):
            if bar.ts_close_ns <= zone.observed_time_ns:
                continue
            swept = bar.low < zone.lower if zone.side is ZoneSide.SUPPORT else bar.high > zone.upper
            if swept:
                zone.consumed = True
                zone.consumed_time_ns = bar.ts_close_ns
                self._active.pop(zone_id, None)
                self._inc("horizontal_structure_swept")

    def _candidate_prior_pivots(self, pivot: ObjectiveZone) -> list[tuple[ObjectiveZone, float, float, float]]:
        lower, upper = self._rejection_areas[pivot]
        candidates: list[tuple[ObjectiveZone, float, float, float]] = []
        for prior in self.pivots.zones:
            if prior.zone_id == pivot.zone_id or prior.side is not pivot.side:
                continue
            if prior.formed_index >= pivot.formed_index:
                continue
            if prior.consumed_time_ns is not None and prior.consumed_time_ns < pivot.observed_time_ns:
                continue
            prior_area = self._rejection_areas.get(prior.zone_id)
            if prior_area is None:
                prior_area = self._rejection_area(prior)
                self._rejection_areas[prior.zone_id] = prior_area
            shared_lower = max(lower, prior_area[0])
            shared_upper = min(upper, prior_area[1])
            if shared_lower < shared_upper:
                candidates.append((prior, shared_lower, shared_upper, shared_upper - shared_lower))
        return candidates

    def _create_structure(self, pivot: ObjectiveZone) -> HorizontalStructureZone | None:
        self._rejection_areas[pivot.zone_id] = self._rejection_area(pivot)
        candidates = self._candidate_prior_pivots(pivot)
        if not candidates:
            return None
        # Geometry decides which prior defense belongs to this second touch:
        # first maximize the shared rejection area, then favor the more
        # structurally confirmed and more separated pivot.  No outcome data is
        # used.
        prior, lower, upper, _ = max(
            candidates,
            key=lambda item: (
                item[3],
                min(item[0].pivot_span, pivot.pivot_span),
                pivot.formed_index - item[0].formed_index,
                item[0].zone_id,
            ),
        )
        side_name = "SUPPORT" if pivot.side is ZoneSide.SUPPORT else "RESISTANCE"
        zone_id = (
            f"{self.symbol}:{self.timeframe_minutes}m:HORIZONTAL_{side_name}:"
            f"{prior.formed_index}-{pivot.formed_index}"
        )
        if any(zone.zone_id == zone_id for zone in self.zones):
            return None
        invalidation = lower - self.tick_size if pivot.side is ZoneSide.SUPPORT else upper + self.tick_size
        structure = HorizontalStructureZone(
            zone_id=zone_id,
            kind=pivot.kind,
            side=pivot.side,
            timeframe_minutes=self.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=lower if pivot.side is ZoneSide.SUPPORT else upper,
            formed_index=pivot.formed_index,
            formed_time_ns=pivot.formed_time_ns,
            observed_time_ns=max(prior.observed_time_ns, pivot.observed_time_ns),
            formation_indices=(prior.formed_index, pivot.formed_index),
            strength_ratio=min(prior.strength_ratio, pivot.strength_ratio),
            pivot_span=max(prior.pivot_span, pivot.pivot_span),
            first_pivot_id=prior.zone_id,
            second_pivot_id=pivot.zone_id,
        )
        self.zones.append(structure)
        self._active[structure.zone_id] = structure
        self._inc("horizontal_structure_confirmed")
        return structure

    def on_bar(self, bar: Candle) -> list[HorizontalStructureZone]:
        if self.pivots.bars and bar.ts_close_ns <= self.pivots.bars[-1].ts_close_ns:
            raise ValueError("source bars must arrive in strictly increasing close time")
        # CausalLiquidityDetector performs the price observation exactly once.
        new_pivots = self.pivots.on_bar(bar)
        self._last_observed_price_time_ns = max(self._last_observed_price_time_ns, bar.ts_close_ns)
        for zone_id, zone in list(self._active.items()):
            if bar.ts_close_ns <= zone.observed_time_ns:
                continue
            swept = bar.low < zone.lower if zone.side is ZoneSide.SUPPORT else bar.high > zone.upper
            if swept:
                zone.consumed = True
                zone.consumed_time_ns = bar.ts_close_ns
                self._active.pop(zone_id, None)
                self._inc("horizontal_structure_swept")
        created: list[HorizontalStructureZone] = []
        for pivot in new_pivots:
            structure = self._create_structure(pivot)
            if structure is not None:
                created.append(structure)
        return created

    def active_zones(self, *, side: ZoneSide | None = None) -> list[HorizontalStructureZone]:
        return [zone for zone in self._active.values() if side is None or zone.side is side]

    def target_zones(self) -> Iterable[HorizontalStructureZone | ObjectiveZone]:
        # Contexts need two defenses; objectives may be any confirmed meaningful
        # swing already visible before the episode.
        yield from self.zones
        yield from self.pivots.zones


class StrongHorizontalSweepScenarioEngine(HorizontalSweepScenarioEngine):
    """Fakeout/Trap engine whose context is a repeated horizontal defense."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.level_detector = HorizontalStructureDetector(
            self.symbol,
            self.context_minutes,
            self.tick_size,
        )

    def _new_sweeps(self, bar: Candle, index: int) -> None:
        candidates: dict[ZoneSide, list[Any]] = {}
        for setup in list(self._active.values()):
            if setup.state is not HorizontalState.WAITING_SWEEP:
                continue
            if bar.ts_close_ns <= setup.observed_time_ns:
                continue
            level = setup.level
            if level.consumed_time_ns is not None and level.consumed_time_ns < bar.ts_close_ns:
                self._finish(setup, HorizontalState.INVALIDATED, bar, "horizontal_level_spent_before_sweep")
                continue
            swept = bar.low < level.lower if level.side is ZoneSide.SUPPORT else bar.high > level.upper
            if swept:
                candidates.setdefault(level.side, []).append(setup)

        for side, group in candidates.items():
            if side is ZoneSide.SUPPORT:
                selected = max(group, key=lambda item: (item.level.upper, item.level.pivot_span))
            else:
                selected = min(group, key=lambda item: (item.level.lower, -item.level.pivot_span))
            for duplicate in group:
                if duplicate is selected:
                    continue
                self._finish(
                    duplicate,
                    HorizontalState.DUPLICATE_EPISODE,
                    bar,
                    "horizontal_levels_collapsed",
                    selected_setup_id=selected.setup_id,
                )
            selected.sweep_time_ns = bar.ts_close_ns
            selected.sweep_index = index
            selected.sweep_extreme = bar.low if side is ZoneSide.SUPPORT else bar.high
            reclaimed = bar.close > selected.level.upper if side is ZoneSide.SUPPORT else bar.close < selected.level.lower
            if reclaimed:
                selected.reclaim_time_ns = bar.ts_close_ns
                selected.state = HorizontalState.WAITING_DISPLACEMENT
                self._inc("horizontal_reclaim_confirmed")
                self._trace("horizontal_reclaim_confirmed", bar.ts_close_ns, selected)
            else:
                selected.state = HorizontalState.WAITING_RECLAIM
                self._inc("horizontal_sweep_unresolved")
                self._trace("horizontal_sweep_unresolved", bar.ts_close_ns, selected)

    def _target(self, setup: Any, bar: Candle, entry: float) -> tuple[Any, float] | None:
        if setup.sweep_time_ns is None:
            return None
        side = self._trade_side(setup.level)
        wanted = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        candidates: list[tuple[float, Any]] = []
        for zone in self.level_detector.target_zones():
            if zone.side is not wanted or zone.observed_time_ns >= setup.sweep_time_ns:
                continue
            if zone.consumed_time_ns is not None and zone.consumed_time_ns < setup.sweep_time_ns:
                continue
            price = zone.lower if side is Side.LONG else zone.upper
            if side is Side.LONG and price > max(entry, bar.high):
                candidates.append((price, zone))
            elif side is Side.SHORT and price < min(entry, bar.low):
                candidates.append((price, zone))
        if not candidates:
            return None
        if side is Side.LONG:
            price, zone = min(candidates, key=lambda item: (item[0], -item[1].pivot_span))
        else:
            price, zone = max(candidates, key=lambda item: (item[0], item[1].pivot_span))
        return zone, price

    def find_zone(self, zone_id: str) -> HorizontalStructureZone | ObjectiveZone | PriceZone | None:
        for zone in self.level_detector.zones:
            if zone.zone_id == zone_id:
                return zone
        for zone in self.level_detector.pivots.zones:
            if zone.zone_id == zone_id:
                return zone
        for zone in self.trigger_detector.zones:
            if zone.zone_id == zone_id:
                return zone
        return None


class StrongResearchScenarioBundle(ResearchScenarioBundle):
    """Overlap scenarios plus repeated-defense Fakeout/Trap scenarios."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.horizontal_macro = StrongHorizontalSweepScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO_HORIZONTAL_STRUCTURE",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_micro = StrongHorizontalSweepScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO_HORIZONTAL_STRUCTURE",
            context_minutes=15,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
