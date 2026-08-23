"""Independent high-quality order-block decision areas over natural geometry.

A visible 15-minute engulfing order block is a primary decision area in several
supplied trades, not merely an annotation on a diagonal line.  This module adds
that mechanism without changing the already proven complete-family router:

* only a high-quality 15m engulfing OB becomes a boundary;
* it must pre-exist and only its first later interaction owns an episode;
* the current confirmed 15m structure side must agree;
* lower-frame source-candle location, first response, decision-swing stop and
  first meaningful objective remain mandatory;
* accepted horizontal S/R flips continue to belong to the repeated-defense
  family, so the OB family emits only bounce/reclaim states.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioPath, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_adjacent import SourceCandleLocatedMixin
from easychart_re1_confirmed import ConfirmedRepeatedDefenseScenarioEngine
from easychart_re1_geometry_v2 import EasyChartRE1GeometryV2Bundle, NaturalGeometryV2Mixin
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneKind, ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


STRONG_DECISION_OB_V2_RULE = (
    "SOURCE_EXPLICIT:"
    "PREEXISTING_HIGH_QUALITY_FIFTEEN_MINUTE_ENGULFING_ORDER_BLOCK_IS_A_FIRST_TOUCH_DECISION_AREA"
)
DECISION_OB_LOCAL_ALIGNMENT_V2_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ORDER_BLOCK_DECISION_AREA_BOUNCE_OR_RECLAIM_MUST_MATCH_CURRENT_CONFIRMED_FIFTEEN_MINUTE_STRUCTURE_SIDE"
)
if STRONG_DECISION_OB_V2_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (STRONG_DECISION_OB_V2_RULE,)
if DECISION_OB_LOCAL_ALIGNMENT_V2_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (DECISION_OB_LOCAL_ALIGNMENT_V2_RULE,)


@dataclass(slots=True)
class DecisionOrderBlockV2:
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


class OrderBlockDecisionStructureBookV2(NearestAnyPivotStructureBook):
    """Pivot objectives plus first-touch high-quality 15m OB boundaries."""

    def __init__(self, symbol: str, timeframe_minutes: int, tick_size: float) -> None:
        super().__init__(symbol, timeframe_minutes, tick_size)
        self.zone_detector = EasyChartZoneDetector(symbol, timeframe_minutes, tick_size)
        self.levels: list[DecisionOrderBlockV2] = []
        self._active_levels: dict[str, DecisionOrderBlockV2] = {}
        self._source_ids: set[str] = set()
        self._decision_counts: dict[str, int] = {}

    def _decision_inc(self, key: str) -> None:
        self._decision_counts[key] = self._decision_counts.get(key, 0) + 1

    def _register(self, zone: PriceZone) -> DecisionOrderBlockV2 | None:
        if (
            zone.kind is not ZoneKind.ORDER_BLOCK
            or not bool(getattr(zone, "high_quality_by_size", False))
            or zone.zone_id in self._source_ids
        ):
            return None
        level_id = f"DECISION_OB_V2:{zone.zone_id}"
        level = DecisionOrderBlockV2(
            level_id=level_id,
            source_zone_id=zone.zone_id,
            side=zone.side,
            lower=zone.lower,
            upper=zone.upper,
            invalidation=zone.invalidation,
            impulse_extreme=float(getattr(zone, "impulse_extreme", zone.invalidation)),
            formed_index=zone.formed_index,
            formed_time_ns=zone.formed_time_ns,
            observed_time_ns=zone.observed_time_ns,
            formation_indices=tuple(
                getattr(zone, "formation_indices", (zone.formed_index,)),
            ),
            strength_ratio=float(getattr(zone, "strength_ratio", 1.0)),
        )
        self._source_ids.add(zone.zone_id)
        self.levels.append(level)
        self._active_levels[level.level_id] = level
        self._decision_inc("strong_decision_ob_created")
        return level

    def on_bar(self, bar: Candle):  # type: ignore[no-untyped-def]
        result = super().on_bar(bar)
        for zone in self.zone_detector.on_bar(bar):
            self._register(zone)
        return result

    def _snapshot(self, level: DecisionOrderBlockV2, time_ns: int) -> StructureZone:
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
                self._decision_inc("decision_ob_first_interaction_retired")
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
        return (
            self._snapshot(selected, interaction_time_ns),
            selected.lower if side is Side.LONG else selected.upper,
        )

    def target_for(self, side: Side, **kwargs):  # type: ignore[no-untyped-def]
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
            self._decision_inc("decision_ob_target_selected")
            return ob
        first = ob[1] < pivot[1] if side is Side.LONG else ob[1] > pivot[1]
        if first:
            self._decision_inc("decision_ob_target_selected")
            return ob
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

    @property
    def decision_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._decision_counts.items())),
            "created": len(self.levels),
            "active": len(self._active_levels),
            "zone_detector": dict(self.zone_detector.diagnostics),
            "rule_provenance": STRONG_DECISION_OB_V2_RULE,
        }


class OrderBlockDecisionScenarioEngineV2(
    NaturalGeometryV2Mixin,
    SourceCandleLocatedMixin,
    ConfirmedRepeatedDefenseScenarioEngine,
):
    """Complete lower-frame response state machine over strong OB boundaries."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = OrderBlockDecisionStructureBookV2(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class EasyChartRE1DecisionAreaV2Bundle(EasyChartRE1GeometryV2Bundle):
    """Proven complete router plus a strong-OB first-touch family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.decision_area = OrderBlockDecisionScenarioEngineV2(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB_V2",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["decision_area"] = 0
        self._decision_area_trace: list[dict[str, Any]] = []
        self._decision_area_counts: dict[str, int] = {}

    def _decision_area_inc(self, key: str) -> None:
        self._decision_area_counts[key] = self._decision_area_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.decision_area.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.decision_area.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        routed = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return routed
        raw = self.decision_area.on_bar(timeframe_minutes, bar)
        self._sync_audit("decision_area", self.decision_area)
        output: list[V5TradePlan] = []
        executable = {ScenarioPath.REJECTION.value, ScenarioPath.ROTATION.value}
        for plan in sorted(
            raw,
            key=lambda item: (item.interaction_time_ns, item.observed_time_ns, item.plan_id),
        ):
            if plan.scenario_path not in executable:
                self._decision_area_inc("non_bounce_or_reclaim_suppressed")
                continue
            if not bool(
                self._local_side is plan.side
                and self._local_break_time_ns is not None
                and plan.observed_time_ns >= self._local_break_time_ns
            ):
                self._decision_area_inc("decision_ob_deferred_against_local_structure")
                continue
            if self._duplicate_episode(plan):
                self._decision_area_inc("decision_ob_overlapped_existing_family")
                continue
            self._claim_episode(plan)
            if self._route_plan(plan):
                output.append(plan)
                self._decision_area_inc("decision_ob_plan_allowed")
                self._decision_area_trace.append(
                    {
                        "scenario_kind": "decision_ob_v2_plan_allowed",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "scenario_path": plan.scenario_path,
                        "entry": plan.entry,
                        "stop": plan.stop,
                        "target": plan.target,
                        "gross_rr": plan.gross_rr,
                        "rule_provenance": DECISION_OB_LOCAL_ALIGNMENT_V2_RULE,
                    },
                )
        return routed + output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self.decision_area.drain_trace() + self._decision_area_trace
        self._decision_area_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.decision_area.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["decision_area_ob_v2_family"] = {
            "counts": dict(sorted(self._decision_area_counts.items())),
            "structure": self.decision_area.structure.decision_diagnostics,
            "engine": self.decision_area.diagnostics,
            "geometry": self.decision_area.natural_geometry_diagnostics,
            "rules": (STRONG_DECISION_OB_V2_RULE, DECISION_OB_LOCAL_ALIGNMENT_V2_RULE),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DecisionAreaV2Bundle
