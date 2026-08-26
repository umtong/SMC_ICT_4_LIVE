"""Repeated-defense horizontal structure family for EasyChart RE1.

The supplied material does not treat every isolated swing as major support or
resistance. Its horizontal examples show a visible area defended repeatedly,
then a sweep/reclaim or a genuine break/retest. A human naturally groups the
nearby wick rejections into one area; software must state that grouping without
turning it into a fitted distance threshold.

RE1 uses a price-geometric translation:

* a confirmed LOW contributes its lower-wick rejection band
  ``[low, min(open, close)]``;
* a confirmed HIGH contributes its upper-wick rejection band
  ``[max(open, close), high]``;
* two same-side, same-scale confirmed pivots form a repeated-defense area only
  when those wick bands overlap and an opposite pivot lies between them;
* each pivot can establish at most one such area;
* the first later interaction owns one causal episode and retires the area.

The resulting area reuses the existing EasyChart state machine: sweep/reclaim,
controlled bounce, event-local OB/FVG, first distinct retest, structural stop,
and a pre-existing opposing objective. It is an independent opportunity family,
not another condition added to the diagonal core.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import (
    ObjectKind,
    Pivot,
    ScenarioSetup,
    StructureFamily,
    StructureZone,
    V5TradePlan,
)
from domain import Candle, Side
from easychart_re1_fresh import EasyChartRE1FreshBundle
from easychart_zones import ZoneSide
from scenario_close_detached_v14 import CloseDetachedRetestScenarioEngine
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


REPEATED_DEFENSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "HORIZONTAL_STRUCTURE_REQUIRES_TWO_OVERLAPPING_WICK_REJECTION_BANDS_WITH_INTERVENING_OPPOSITE_PIVOT"
)
REPEATED_DEFENSE_LIFECYCLE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_LATER_INTERACTION_RETIRES_REPEATED_DEFENSE_HORIZONTAL_AREA"
)
HORIZONTAL_FLIP_STOP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTED_HORIZONTAL_BREAK_RETEST_STOP_LIES_BEYOND_RETEST_EXTREME_AND_LEVEL"
)
for _rule in (
    REPEATED_DEFENSE_RULE,
    REPEATED_DEFENSE_LIFECYCLE_RULE,
    HORIZONTAL_FLIP_STOP_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(slots=True)
class RepeatedDefenseLevel:
    level_id: str
    side: ZoneSide
    lower: float
    upper: float
    invalidation: float
    first_pivot_id: str
    second_pivot_id: str
    opposite_pivot_id: str
    first_index: int
    second_index: int
    opposite_index: int
    formed_time_ns: int
    observed_time_ns: int
    pivot_span: int
    strength_ratio: float
    first_touch_time_ns: int | None = None
    consumed_time_ns: int | None = None

    @property
    def active(self) -> bool:
        return self.consumed_time_ns is None


class RepeatedDefenseStructureBook(NearestAnyPivotStructureBook):
    """Causal horizontal areas made from overlapping repeated wick defense."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.defense_levels: list[RepeatedDefenseLevel] = []
        self._active_defense: dict[str, RepeatedDefenseLevel] = {}
        self._claimed_defense_pivots: set[str] = set()

    def _pivot_band(self, pivot: Pivot) -> tuple[float, float]:
        bar = self.bars[pivot.index]
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        if pivot.side == "LOW":
            return bar.low, body_low
        return body_high, bar.high

    def _opposite_between_for_level(self, first: Pivot, second: Pivot) -> Pivot | None:
        wanted = "HIGH" if first.side == "LOW" else "LOW"
        candidates = [
            pivot
            for pivot in self.pivots
            if pivot.side == wanted
            and first.index < pivot.index < second.index
            and pivot.observed_time_ns <= second.observed_time_ns
        ]
        return max(
            candidates,
            key=lambda item: (item.span, item.strength_ratio, item.index, item.pivot_id),
            default=None,
        )

    def _compatible_first(self, second: Pivot) -> tuple[Pivot, Pivot, float, float] | None:
        if second.pivot_id in self._claimed_defense_pivots:
            return None
        second_lower, second_upper = self._pivot_band(second)
        candidates: list[tuple[Pivot, Pivot, float, float]] = []
        for first in self.pivots:
            if (
                first.pivot_id == second.pivot_id
                or first.pivot_id in self._claimed_defense_pivots
                or first.side != second.side
                or first.span != second.span
                or first.index >= second.index
            ):
                continue
            opposite = self._opposite_between_for_level(first, second)
            if opposite is None:
                continue
            first_lower, first_upper = self._pivot_band(first)
            lower = max(first_lower, second_lower)
            upper = min(first_upper, second_upper)
            # A one-tick-or-wider common rejection band is a price fact, not an
            # ATR/percentage tolerance fitted to an evaluation period.
            if upper - lower + 1e-12 < self.tick_size:
                continue
            candidates.append((first, opposite, lower, upper))
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                item[0].index,
                item[0].span,
                item[0].strength_ratio,
                item[0].pivot_id,
            ),
        )

    def _build_repeated_defense(self, second: Pivot) -> RepeatedDefenseLevel | None:
        match = self._compatible_first(second)
        if match is None:
            return None
        first, opposite, lower, upper = match
        if second.side == "LOW":
            side = ZoneSide.SUPPORT
            invalidation = min(first.price, second.price) - self.tick_size
            suffix = "SUPPORT"
        else:
            side = ZoneSide.RESISTANCE
            invalidation = max(first.price, second.price) + self.tick_size
            suffix = "RESISTANCE"
        level_id = (
            f"{self.symbol}:{self.timeframe_minutes}m:REPEATED_DEFENSE:{suffix}:"
            f"{first.pivot_id}|{opposite.pivot_id}|{second.pivot_id}"
        )
        level = RepeatedDefenseLevel(
            level_id=level_id,
            side=side,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            first_pivot_id=first.pivot_id,
            second_pivot_id=second.pivot_id,
            opposite_pivot_id=opposite.pivot_id,
            first_index=first.index,
            second_index=second.index,
            opposite_index=opposite.index,
            formed_time_ns=second.event_time_ns,
            observed_time_ns=max(
                first.observed_time_ns,
                opposite.observed_time_ns,
                second.observed_time_ns,
            ),
            pivot_span=second.span,
            strength_ratio=min(first.strength_ratio, second.strength_ratio),
        )
        self._claimed_defense_pivots.update((first.pivot_id, second.pivot_id))
        self.defense_levels.append(level)
        self._active_defense[level.level_id] = level
        self._inc(f"repeated_defense_{suffix.lower()}_created")
        return level

    def on_bar(self, bar: Candle):  # type: ignore[no-untyped-def]
        pivots, lines, channels = super().on_bar(bar)
        for pivot in pivots:
            self._build_repeated_defense(pivot)
        return pivots, lines, channels

    def _snapshot(self, level: RepeatedDefenseLevel, time_ns: int) -> StructureZone:
        kind = (
            ObjectKind.HORIZONTAL_SUPPORT
            if level.side is ZoneSide.SUPPORT
            else ObjectKind.HORIZONTAL_RESISTANCE
        )
        return StructureZone(
            zone_id=f"{level.level_id}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=level.side,
            timeframe_minutes=self.timeframe_minutes,
            lower=level.lower,
            upper=level.upper,
            invalidation=level.invalidation,
            impulse_extreme=(
                level.invalidation + self.tick_size
                if level.side is ZoneSide.SUPPORT
                else level.invalidation - self.tick_size
            ),
            formed_index=level.second_index,
            formed_time_ns=level.formed_time_ns,
            observed_time_ns=level.observed_time_ns,
            formation_indices=(
                level.first_index,
                level.opposite_index,
                level.second_index,
            ),
            strength_ratio=level.strength_ratio,
            source_structure_id=level.level_id,
            source_pivot_span=level.pivot_span,
            first_touch_time_ns=level.first_touch_time_ns,
            consumed=not level.active,
        )

    def boundaries_at(self, time_ns: int) -> list[StructureZone]:
        return [
            self._snapshot(level, time_ns)
            for level in self._active_defense.values()
            if level.observed_time_ns < time_ns
        ]

    def snapshot_for(self, zone: StructureZone, time_ns: int) -> StructureZone:
        level = self._active_defense.get(zone.source_structure_id)
        if level is None:
            level = next(
                (
                    item
                    for item in self.defense_levels
                    if item.level_id == zone.source_structure_id
                ),
                None,
            )
        return self._snapshot(level, time_ns) if level is not None else super().snapshot_for(zone, time_ns)

    def observe_price(self, bar: Candle) -> None:
        for level_id, level in list(self._active_defense.items()):
            if bar.ts_close_ns <= level.observed_time_ns:
                continue
            if bar.low <= level.upper and bar.high >= level.lower:
                level.first_touch_time_ns = bar.ts_close_ns
                level.consumed_time_ns = bar.ts_close_ns
                self._active_defense.pop(level_id, None)
                self._inc("repeated_defense_first_interaction_retired")
        # Keep the pivot-objective lifecycle used by the existing target policy.
        super().observe_price(bar)

    def _defense_target_for(
        self,
        side: Side,
        *,
        interaction_time_ns: int,
        current_high: float,
        current_low: float,
    ) -> tuple[StructureZone, float] | None:
        wanted = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        candidates = [
            level
            for level in self._active_defense.values()
            if level.side is wanted
            and level.observed_time_ns < interaction_time_ns
            and (
                (side is Side.LONG and level.lower > current_high)
                or (side is Side.SHORT and level.upper < current_low)
            )
        ]
        if not candidates:
            return None
        selected = (
            min(candidates, key=lambda item: (item.lower, -item.pivot_span, item.level_id))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item.upper, item.pivot_span, item.level_id))
        )
        price = selected.lower if side is Side.LONG else selected.upper
        return self._snapshot(selected, interaction_time_ns), price

    def target_for(
        self,
        side: Side,
        *,
        interaction_time_ns: int,
        source_span: int,
        current_high: float,
        current_low: float,
    ):
        defense = self._defense_target_for(
            side,
            interaction_time_ns=interaction_time_ns,
            current_high=current_high,
            current_low=current_low,
        )
        pivot = super().target_for(
            side,
            interaction_time_ns=interaction_time_ns,
            source_span=source_span,
            current_high=current_high,
            current_low=current_low,
        )
        if defense is None:
            return pivot
        if pivot is None:
            self._inc("repeated_defense_target_selected")
            return defense
        defense_price = defense[1]
        pivot_price = pivot[1]
        defense_is_first = (
            defense_price < pivot_price
            if side is Side.LONG
            else defense_price > pivot_price
        )
        if defense_is_first:
            self._inc("repeated_defense_target_selected")
            return defense
        return pivot

    def target_spent_after(self, zone: StructureZone, interaction_time_ns: int) -> bool:
        level = next(
            (
                item
                for item in self.defense_levels
                if item.level_id == zone.source_structure_id
            ),
            None,
        )
        if level is not None:
            return bool(
                level.consumed_time_ns is not None
                and level.consumed_time_ns > interaction_time_ns
            )
        return super().target_spent_after(zone, interaction_time_ns)


class RepeatedDefenseScenarioEngine(CloseDetachedRetestScenarioEngine):
    """The existing complete scenario policy over repeated horizontal areas."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = RepeatedDefenseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )

    def _acceptance_stop(self, setup: ScenarioSetup, time_ns: int) -> float | None:
        bar = self._current_trigger_bar
        if bar is None or bar.ts_close_ns != time_ns:
            raise RuntimeError("horizontal acceptance stop requested without completed retest bar")
        _, lower, upper = self._projected_bounds(setup, time_ns)
        stop = (
            min(lower - self.tick_size, bar.low - self.tick_size)
            if setup.side is Side.LONG
            else max(upper + self.tick_size, bar.high + self.tick_size)
        )
        self._inc("horizontal_acceptance_retest_extreme_stop")
        self._trace(
            "horizontal_acceptance_retest_extreme_stop",
            time_ns,
            setup,
            projected_lower=lower,
            projected_upper=upper,
            retest_low=bar.low,
            retest_high=bar.high,
            executable_stop=stop,
            rule_provenance=HORIZONTAL_FLIP_STOP_RULE,
        )
        return stop


class EasyChartRE1IntegratedBundle(EasyChartRE1FreshBundle):
    """Stable diagonal core plus an independent repeated-defense family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.horizontal = RepeatedDefenseScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["horizontal"] = 0
        self._horizontal_bundle_trace: list[dict[str, Any]] = []

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.horizontal.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.horizontal.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        diagonal = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return diagonal

        horizontal_raw = self.horizontal.on_bar(timeframe_minutes, bar)
        self._sync_audit("horizontal", self.horizontal)
        horizontal: list[V5TradePlan] = []
        for plan in sorted(
            horizontal_raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            # Diagonal gets precedence only when both engines describe the same
            # side, time and overlapping price episode. Otherwise the families
            # remain independent candidates for the account router.
            if self._duplicate_episode(plan):
                self._horizontal_bundle_trace.append(
                    {
                        "scenario_kind": "horizontal_episode_overlapped_existing_family",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "overlap_lower": plan.overlap_lower,
                        "overlap_upper": plan.overlap_upper,
                    },
                )
                continue
            self._claim_episode(plan)
            if self._route_plan(plan):
                horizontal.append(plan)
        return diagonal + horizontal

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self.horizontal.drain_trace() + self._horizontal_bundle_trace
        self._horizontal_bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.horizontal.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["horizontal_family"] = {
            "policy": "TWO_OVERLAPPING_WICK_REJECTION_BANDS_THEN_FIRST_LATER_INTERACTION",
            "engine": dict(self.horizontal.diagnostics),
            "structure": dict(self.horizontal.structure.diagnostics),
            "levels_created": len(self.horizontal.structure.defense_levels),
            "levels_active_at_end": sum(
                item.active for item in self.horizontal.structure.defense_levels
            ),
            "rules": (
                REPEATED_DEFENSE_RULE,
                REPEATED_DEFENSE_LIFECYCLE_RULE,
                HORIZONTAL_FLIP_STOP_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1IntegratedBundle
