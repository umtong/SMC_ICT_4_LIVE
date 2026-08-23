"""Mechanism-complete, fixed-plan EasyChart RE1 policy.

The core candidate is deliberately small:

* diagonal/channel sweeps and rejections remain the location family;
* generic single-line accepted breaks are deferred because they did not
  distinguish a true S/R flip from temporary price acceptance;
* a repeatedly defended horizontal area owns the accepted-break/retest family;
* a high-quality 15-minute engulfing order block owns the continuation-bounce
  and sweep/reclaim decision-area family;
* every entry still requires an event-local one-minute footprint, first
  detached retest, immediate response, natural structural invalidation and the
  first meaningful pre-entry objective.

No score, fitted volatility threshold, clock timeout, fixed-R target, partial
exit, stop ratchet, daily rule or trade-count rule is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioPath, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_natural_geometry import (
    EasyChartRE1NaturalGeometryBundle,
    EpisodeLocalFVGMixin,
    NaturalGeometryMixin,
    NaturalHorizontalEngine,
)
from easychart_re1_confirmed import ConfirmedRepeatedDefenseScenarioEngine
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneKind, ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


SOURCE_FOOTPRINT_LOCATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_OB_SOURCE_CANDLE_OR_FVG_DISPLACEMENT_CANDLE_TOUCHES_THE_DECISION_AREA_AND_ITS_IMPULSE_CLOSES_AWAY"
)
STRONG_DECISION_OB_RULE = (
    "SOURCE_EXPLICIT:"
    "PREEXISTING_HIGH_QUALITY_FIFTEEN_MINUTE_ENGULFING_ORDER_BLOCK_IS_A_FIRST_TOUCH_DECISION_AREA"
)
DECISION_OB_ALIGNMENT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ORDER_BLOCK_DECISION_AREA_BOUNCE_OR_SWEEP_RECLAIM_MATCHES_CURRENT_CONFIRMED_FIFTEEN_MINUTE_STRUCTURE_SIDE"
)
HORIZONTAL_FLIP_RESPONSIBILITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTED_BREAK_RETEST_IS_EXECUTABLE_AT_A_REPEATEDLY_DEFENDED_HORIZONTAL_AREA_NOT_AN_ISOLATED_DIAGONAL"
)
if STRONG_DECISION_OB_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (STRONG_DECISION_OB_RULE,)
for _rule in (SOURCE_FOOTPRINT_LOCATION_RULE, DECISION_OB_ALIGNMENT_RULE, HORIZONTAL_FLIP_RESPONSIBILITY_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class SourceFootprintLocatedMixin:
    """Require the footprint's causal source candle itself at the structure."""

    def _formation_touches_context(self, zone: PriceZone, setup: Any) -> bool:
        formation = tuple(zone.formation_indices)
        if not formation:
            return False
        if zone.kind is ZoneKind.ORDER_BLOCK:
            source_index = formation[0]
            impulse_index = formation[-1]
        else:
            source_index = formation[1] if len(formation) >= 3 else formation[0]
            impulse_index = source_index
        bars = self.trigger_detector.bars
        if not (0 <= source_index < len(bars) and 0 <= impulse_index < len(bars)):
            return False
        source = bars[source_index]
        impulse = bars[impulse_index]
        _, lower, upper = self._projected_bounds(setup, source.ts_close_ns)
        touches = source.low <= upper and source.high >= lower
        closes_away = impulse.close > upper if setup.side is Side.LONG else impulse.close < lower
        if not touches:
            self._inc("footprint_source_candle_not_at_structure_deferred")
        elif not closes_away:
            self._inc("footprint_impulse_did_not_close_away_deferred")
        return touches and closes_away


@dataclass(slots=True)
class DecisionOrderBlock:
    level_id: str
    source_zone_id: str
    side: ZoneSide
    lower: float
    upper: float
    invalidation: float
    impulse_extreme: float
    formed_index: int
    formed_time_ns: int
    observed_time_ns: int
    formation_indices: tuple[int, ...]
    strength_ratio: float
    first_touch_time_ns: int | None = None
    consumed_time_ns: int | None = None

    @property
    def active(self) -> bool:
        return self.consumed_time_ns is None


class OrderBlockDecisionStructureBook(NearestAnyPivotStructureBook):
    """Pivot objectives plus first-touch high-quality 15m OB boundaries."""

    def __init__(self, symbol: str, timeframe_minutes: int, tick_size: float) -> None:
        super().__init__(symbol, timeframe_minutes, tick_size)
        self.zone_detector = EasyChartZoneDetector(symbol, timeframe_minutes, tick_size)
        self.levels: list[DecisionOrderBlock] = []
        self._active_levels: dict[str, DecisionOrderBlock] = {}
        self._source_ids: set[str] = set()
        self._decision_counts: dict[str, int] = {}

    def _dinc(self, key: str) -> None:
        self._decision_counts[key] = self._decision_counts.get(key, 0) + 1

    def _register(self, zone: PriceZone) -> None:
        if zone.kind is not ZoneKind.ORDER_BLOCK or not zone.high_quality_by_size or zone.zone_id in self._source_ids:
            return
        level = DecisionOrderBlock(
            level_id=f"DECISION_OB:{zone.zone_id}",
            source_zone_id=zone.zone_id,
            side=zone.side,
            lower=zone.lower,
            upper=zone.upper,
            invalidation=zone.invalidation,
            impulse_extreme=zone.impulse_extreme,
            formed_index=zone.formed_index,
            formed_time_ns=zone.formed_time_ns,
            observed_time_ns=zone.observed_time_ns,
            formation_indices=tuple(zone.formation_indices),
            strength_ratio=zone.strength_ratio,
        )
        self._source_ids.add(zone.zone_id)
        self.levels.append(level)
        self._active_levels[level.level_id] = level
        self._dinc("strong_decision_ob_created")

    def on_bar(self, bar: Candle):
        result = super().on_bar(bar)
        for zone in self.zone_detector.on_bar(bar):
            self._register(zone)
        return result

    def _snapshot(self, level: DecisionOrderBlock, time_ns: int) -> StructureZone:
        kind = ObjectKind.HORIZONTAL_SUPPORT if level.side is ZoneSide.SUPPORT else ObjectKind.HORIZONTAL_RESISTANCE
        return StructureZone(
            zone_id=f"{level.level_id}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=level.side,
            timeframe_minutes=self.timeframe_minutes,
            lower=level.lower,
            upper=level.upper,
            invalidation=level.invalidation,
            impulse_extreme=level.impulse_extreme,
            formed_index=level.formed_index,
            formed_time_ns=level.formed_time_ns,
            observed_time_ns=level.observed_time_ns,
            formation_indices=level.formation_indices,
            strength_ratio=level.strength_ratio,
            source_structure_id=level.level_id,
            source_pivot_span=2,
            first_touch_time_ns=level.first_touch_time_ns,
            consumed=not level.active,
        )

    def boundaries_at(self, time_ns: int) -> list[StructureZone]:
        return [self._snapshot(level, time_ns) for level in self._active_levels.values() if level.observed_time_ns < time_ns]

    def snapshot_for(self, zone: StructureZone, time_ns: int) -> StructureZone:
        level = self._active_levels.get(zone.source_structure_id)
        if level is None:
            level = next((item for item in self.levels if item.level_id == zone.source_structure_id), None)
        return self._snapshot(level, time_ns) if level is not None else super().snapshot_for(zone, time_ns)

    def observe_price(self, bar: Candle) -> None:
        for level_id, level in list(self._active_levels.items()):
            if bar.ts_close_ns <= level.observed_time_ns:
                continue
            if bar.low <= level.upper and bar.high >= level.lower:
                level.first_touch_time_ns = bar.ts_close_ns
                level.consumed_time_ns = bar.ts_close_ns
                self._active_levels.pop(level_id, None)
                self._dinc("decision_ob_first_interaction_retired")
        super().observe_price(bar)

    def _ob_target_for(
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
            and ((side is Side.LONG and level.lower > current_high) or (side is Side.SHORT and level.upper < current_low))
        ]
        if not candidates:
            return None
        selected = (
            min(candidates, key=lambda item: (item.lower, -item.strength_ratio, item.level_id))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item.upper, item.strength_ratio, item.level_id))
        )
        return self._snapshot(selected, interaction_time_ns), selected.lower if side is Side.LONG else selected.upper

    def target_for(self, side: Side, **kwargs):
        ob = self._ob_target_for(
            side,
            interaction_time_ns=kwargs["interaction_time_ns"],
            current_high=kwargs["current_high"],
            current_low=kwargs["current_low"],
        )
        pivot = super().target_for(side, **kwargs)
        if ob is None:
            return pivot
        if pivot is None:
            self._dinc("decision_ob_target_selected")
            return ob
        if (ob[1] < pivot[1]) if side is Side.LONG else (ob[1] > pivot[1]):
            self._dinc("decision_ob_target_selected")
            return ob
        return pivot

    def target_spent_after(self, zone: StructureZone, interaction_time_ns: int) -> bool:
        level = next((item for item in self.levels if item.level_id == zone.source_structure_id), None)
        if level is not None:
            return bool(level.consumed_time_ns is not None and level.consumed_time_ns > interaction_time_ns)
        return super().target_spent_after(zone, interaction_time_ns)

    @property
    def decision_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._decision_counts.items())),
            "created": len(self.levels),
            "active": len(self._active_levels),
            "zone_detector": dict(self.zone_detector.diagnostics),
            "rule_provenance": STRONG_DECISION_OB_RULE,
        }


class DecisionAreaEngine(
    NaturalGeometryMixin,
    EpisodeLocalFVGMixin,
    SourceFootprintLocatedMixin,
    ConfirmedRepeatedDefenseScenarioEngine,
):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = OrderBlockDecisionStructureBook(self.symbol, self.higher_minutes, self.tick_size)


class LocatedHorizontalFlipEngine(SourceFootprintLocatedMixin, NaturalHorizontalEngine):
    pass


class EasyChartRE1CompletePolicyBundle(EasyChartRE1NaturalGeometryBundle):
    """Natural fixed-plan core plus OB decisions and horizontal S/R flips."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.decision_area = DecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_flip = LocatedHorizontalFlipEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["decision_area"] = 0
        self._audit_offsets["horizontal_flip"] = 0
        self._complete_trace: list[dict[str, Any]] = []
        self._complete_counts: dict[str, int] = {}
        self._local_break_time_ns: int | None = None

    def _cinc(self, key: str) -> None:
        self._complete_counts[key] = self._complete_counts.get(key, 0) + 1

    def _advance_local_direction(self, bar: Candle) -> None:
        before = None if self._last_local_direction_pivot is None else self._last_local_direction_pivot.pivot_id
        super()._advance_local_direction(bar)
        after = None if self._last_local_direction_pivot is None else self._last_local_direction_pivot.pivot_id
        if after is not None and after != before:
            self._local_break_time_ns = bar.ts_close_ns

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if plan.scale_name == "MICRO" and plan.scenario_path == ScenarioPath.ACCEPTANCE.value:
            self._cinc("isolated_diagonal_acceptance_deferred")
            self._complete_trace.append(
                {
                    "scenario_kind": "isolated_diagonal_acceptance_deferred",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "rule_provenance": HORIZONTAL_FLIP_RESPONSIBILITY_RULE,
                }
            )
            return False
        return super()._route_plan(plan)

    @property
    def setups(self):
        return super().setups + self.decision_area.setups + self.horizontal_flip.setups

    @property
    def plans(self):
        return super().plans + self.decision_area.plans + self.horizontal_flip.plans

    def _route_decision_area(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(raw, key=lambda item: (item.interaction_time_ns, item.observed_time_ns, item.plan_id)):
            if plan.scenario_path not in {ScenarioPath.BOUNCE.value, ScenarioPath.REJECTION.value}:
                self._cinc("decision_ob_non_bounce_or_sweep_suppressed")
                continue
            aligned = (
                self._local_side is not None
                and self._local_side is plan.side
                and self._local_break_time_ns is not None
                and plan.observed_time_ns >= self._local_break_time_ns
            )
            if not aligned:
                self._cinc("decision_ob_deferred_against_local_structure")
                continue
            if self._duplicate_episode(plan):
                self._cinc("decision_ob_overlapped_existing_family")
                continue
            self._claim_episode(plan)
            if super()._route_plan(plan):
                output.append(plan)
                self._cinc("decision_ob_plan_allowed")
        return output

    def _route_horizontal_flip(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(raw, key=lambda item: (item.interaction_time_ns, item.observed_time_ns, item.plan_id)):
            if plan.scenario_path != ScenarioPath.ACCEPTANCE.value:
                self._cinc("horizontal_flip_non_acceptance_suppressed")
                continue
            if self._duplicate_episode(plan):
                self._cinc("horizontal_flip_overlapped_existing_family")
                continue
            self._claim_episode(plan)
            if super()._route_plan(plan):
                output.append(plan)
                self._cinc("horizontal_flip_plan_allowed")
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        routed = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return routed
        decision_raw = self.decision_area.on_bar(timeframe_minutes, bar)
        flip_raw = self.horizontal_flip.on_bar(timeframe_minutes, bar)
        self._sync_audit("decision_area", self.decision_area)
        self._sync_audit("horizontal_flip", self.horizontal_flip)
        return routed + self._route_decision_area(decision_raw) + self._route_horizontal_flip(flip_raw)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self.decision_area.drain_trace() + self.horizontal_flip.drain_trace() + self._complete_trace
        self._complete_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.decision_area.find_zone(zone_id) or self.horizontal_flip.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["complete_mechanism_policy"] = {
            "counts": dict(sorted(self._complete_counts.items())),
            "decision_area_structure": self.decision_area.structure.decision_diagnostics,
            "decision_area_engine": self.decision_area.diagnostics,
            "decision_area_geometry": self.decision_area.natural_geometry_diagnostics,
            "horizontal_flip_engine": self.horizontal_flip.diagnostics,
            "rules": (
                SOURCE_FOOTPRINT_LOCATION_RULE,
                STRONG_DECISION_OB_RULE,
                DECISION_OB_ALIGNMENT_RULE,
                HORIZONTAL_FLIP_RESPONSIBILITY_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1CompletePolicyBundle
