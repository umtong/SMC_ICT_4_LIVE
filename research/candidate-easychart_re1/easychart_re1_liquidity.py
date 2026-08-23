"""Independent major-liquidity sweep/reclaim family for EasyChart RE1.

The disclosed failures were all routed as continuation.  Improving that router
alone cannot create opportunities in ranges and turning points.  The supplied
Fakeout/Trap material and the live examples repeatedly use a different causal
mechanism: a visible prior swing holds resting liquidity, price trades through
it, closes back inside, and only then is an event-local OB/FVG retest traded.

This module adds that mechanism as an independent family rather than weakening
continuation rules.  A major 15-minute pivot contributes its actual wick
rejection band.  The first later interaction owns the level; only a sweep and
reclaim can originate an executable plan.  The sweep extreme is the structural
invalidation and the first live 5m/15m opposing structure is the full target.
The same strong located footprint and first-response rules used by the repaired
continuation family apply.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import (
    ObjectKind,
    Pivot,
    ScenarioPath,
    StructureFamily,
    StructureZone,
    V5TradePlan,
)
from domain import Candle, Side
from easychart_re1_ablation import EasyChartRE1LocalAlignmentBundle, EasyChartRE1LocationBundle
from easychart_re1_confirmed import ConfirmedRepeatedDefenseScenarioEngine
from easychart_re1_impulse import FirstObstacleObjectiveMixin, StrongLocatedFootprintMixin
from easychart_zones import ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


MAJOR_LIQUIDITY_RULE = (
    "SOURCE_EXPLICIT:"
    "CONFIRMED_MAJOR_SWING_WICK_BAND_IS_A_LIQUIDITY_LEVEL_TRADED_ONLY_ON_FIRST_LATER_SWEEP_AND_RECLAIM"
)
MAJOR_LIQUIDITY_INDEPENDENCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "MAJOR_SWEEP_RECLAIM_IS_AN_INDEPENDENT_REVERSAL_FAMILY_NOT_A_CONTINUATION_EXCEPTION"
)
for _rule in (MAJOR_LIQUIDITY_RULE,):
    if _rule not in _contracts.SOURCE_RULES:
        _contracts.SOURCE_RULES += (_rule,)
if MAJOR_LIQUIDITY_INDEPENDENCE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (MAJOR_LIQUIDITY_INDEPENDENCE_RULE,)


@dataclass(slots=True)
class MajorLiquidityLevel:
    level_id: str
    pivot_id: str
    side: ZoneSide
    lower: float
    upper: float
    invalidation: float
    pivot_index: int
    formed_time_ns: int
    observed_time_ns: int
    pivot_span: int
    strength_ratio: float
    first_touch_time_ns: int | None = None
    consumed_time_ns: int | None = None

    @property
    def active(self) -> bool:
        return self.consumed_time_ns is None


class MajorLiquidityStructureBook(NearestAnyPivotStructureBook):
    """First-touch lifecycle for span-6 wick rejection liquidity bands."""

    MAJOR_SPAN = 6

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.levels: list[MajorLiquidityLevel] = []
        self._active_levels: dict[str, MajorLiquidityLevel] = {}
        self._registered_pivots: set[str] = set()

    def _register_level(self, pivot: Pivot) -> MajorLiquidityLevel | None:
        if pivot.span != self.MAJOR_SPAN or pivot.pivot_id in self._registered_pivots:
            return None
        bar = self.bars[pivot.index]
        if pivot.side == "LOW":
            side = ZoneSide.SUPPORT
            lower, upper = bar.low, min(bar.open, bar.close)
            invalidation = bar.low - self.tick_size
            suffix = "LOW_SUPPORT"
        else:
            side = ZoneSide.RESISTANCE
            lower, upper = max(bar.open, bar.close), bar.high
            invalidation = bar.high + self.tick_size
            suffix = "HIGH_RESISTANCE"
        # Keep a real one-tick band even when the source candle has no wick.
        if upper - lower < self.tick_size:
            if side is ZoneSide.SUPPORT:
                upper = lower + self.tick_size
            else:
                lower = upper - self.tick_size
        level_id = f"{pivot.pivot_id}:MAJOR_LIQUIDITY:{suffix}"
        level = MajorLiquidityLevel(
            level_id=level_id,
            pivot_id=pivot.pivot_id,
            side=side,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            pivot_index=pivot.index,
            formed_time_ns=pivot.event_time_ns,
            observed_time_ns=pivot.observed_time_ns,
            pivot_span=pivot.span,
            strength_ratio=pivot.strength_ratio,
        )
        self._registered_pivots.add(pivot.pivot_id)
        self.levels.append(level)
        self._active_levels[level.level_id] = level
        self._inc(f"major_liquidity_{suffix.lower()}_created")
        return level

    def on_bar(self, bar: Candle):  # type: ignore[no-untyped-def]
        pivots, lines, channels = super().on_bar(bar)
        for pivot in pivots:
            self._register_level(pivot)
        return pivots, lines, channels

    def _snapshot(self, level: MajorLiquidityLevel, time_ns: int) -> StructureZone:
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
            formed_index=level.pivot_index,
            formed_time_ns=level.formed_time_ns,
            observed_time_ns=level.observed_time_ns,
            formation_indices=(level.pivot_index,),
            strength_ratio=level.strength_ratio,
            source_structure_id=level.level_id,
            source_pivot_span=level.pivot_span,
            first_touch_time_ns=level.first_touch_time_ns,
            consumed=not level.active,
        )

    def boundaries_at(self, time_ns: int) -> list[StructureZone]:
        return [
            self._snapshot(level, time_ns)
            for level in self._active_levels.values()
            if level.observed_time_ns < time_ns
        ]

    def snapshot_for(self, zone: StructureZone, time_ns: int) -> StructureZone:
        level = self._active_levels.get(zone.source_structure_id)
        if level is None:
            level = next(
                (item for item in self.levels if item.level_id == zone.source_structure_id),
                None,
            )
        return self._snapshot(level, time_ns) if level is not None else super().snapshot_for(zone, time_ns)

    def observe_price(self, bar: Candle) -> None:
        for level_id, level in list(self._active_levels.items()):
            if bar.ts_close_ns <= level.observed_time_ns:
                continue
            if bar.low <= level.upper and bar.high >= level.lower:
                level.first_touch_time_ns = bar.ts_close_ns
                level.consumed_time_ns = bar.ts_close_ns
                self._active_levels.pop(level_id, None)
                self._inc("major_liquidity_first_interaction_retired")
        # Preserve the parent pivot objective lifecycle for targets.
        super().observe_price(bar)

    def _level_target_for(
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
            for level in self._active_levels.values()
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
            min(candidates, key=lambda item: (item.lower, -item.strength_ratio, item.level_id))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item.upper, item.strength_ratio, item.level_id))
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
        level = self._level_target_for(
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
        if level is None:
            return pivot
        if pivot is None:
            self._inc("major_liquidity_target_selected")
            return level
        level_first = level[1] < pivot[1] if side is Side.LONG else level[1] > pivot[1]
        if level_first:
            self._inc("major_liquidity_target_selected")
            return level
        return pivot

    def target_spent_after(self, zone: StructureZone, interaction_time_ns: int) -> bool:
        level = next(
            (item for item in self.levels if item.level_id == zone.source_structure_id),
            None,
        )
        if level is not None:
            return bool(
                level.consumed_time_ns is not None
                and level.consumed_time_ns > interaction_time_ns
            )
        return super().target_spent_after(zone, interaction_time_ns)


class MajorLiquidityScenarioEngine(
    FirstObstacleObjectiveMixin,
    StrongLocatedFootprintMixin,
    ConfirmedRepeatedDefenseScenarioEngine,
):
    """Existing causal rejection state machine over major swing liquidity."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = MajorLiquidityStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class _MajorLiquidityIntegratedMixin:
    """Route direct sweeps independently while preserving one account stream."""

    def _init_major_liquidity(self, symbol: str, tick_size: float, minimum_gross_rr: float) -> None:
        self.liquidity = MajorLiquidityScenarioEngine(
            symbol,
            tick_size,
            scale_name="MAJOR_LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["liquidity"] = 0
        self._liquidity_bundle_trace: list[dict[str, Any]] = []

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.liquidity.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.liquidity.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        routed = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return routed
        raw = self.liquidity.on_bar(timeframe_minutes, bar)
        self._sync_audit("liquidity", self.liquidity)
        direct: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path != ScenarioPath.REJECTION.value:
                self._liquidity_bundle_trace.append(
                    {
                        "scenario_kind": "major_liquidity_non_rejection_suppressed",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "scenario_path": plan.scenario_path,
                        "rule_provenance": MAJOR_LIQUIDITY_RULE,
                    },
                )
                continue
            if self._duplicate_episode(plan):
                self._liquidity_bundle_trace.append(
                    {
                        "scenario_kind": "major_liquidity_episode_overlapped_existing_family",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                    },
                )
                continue
            self._claim_episode(plan)
            direct.append(plan)
            self._liquidity_bundle_trace.append(
                {
                    "scenario_kind": "major_liquidity_direct_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "rule_provenance": MAJOR_LIQUIDITY_INDEPENDENCE_RULE,
                },
            )
        return routed + direct

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self.liquidity.drain_trace() + self._liquidity_bundle_trace
        self._liquidity_bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.liquidity.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["major_liquidity_family"] = {
            "policy": "FIRST_MAJOR_SWING_WICK_BAND_SWEEP_RECLAIM_ONLY",
            "engine": self.liquidity.diagnostics,
            "objective": self.liquidity.first_obstacle_diagnostics,
            "rules": (MAJOR_LIQUIDITY_RULE, MAJOR_LIQUIDITY_INDEPENDENCE_RULE),
        }
        return output


class EasyChartRE1LiquidityLocationBundle(_MajorLiquidityIntegratedMixin, EasyChartRE1LocationBundle):
    """Repaired persistent continuation plus independent major sweeps."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._init_major_liquidity(symbol, tick_size, minimum_gross_rr)


class EasyChartRE1LiquidityLocalBundle(_MajorLiquidityIntegratedMixin, EasyChartRE1LocalAlignmentBundle):
    """Current-local continuation plus independent major sweeps."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._init_major_liquidity(symbol, tick_size, minimum_gross_rr)


__all__ = [
    "EasyChartRE1LiquidityLocationBundle",
    "EasyChartRE1LiquidityLocalBundle",
    "MajorLiquidityScenarioEngine",
    "MajorLiquidityStructureBook",
]
