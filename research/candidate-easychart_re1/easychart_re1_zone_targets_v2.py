"""Pre-existing OB/FVG objectives over the proven natural-geometry router.

The first realistic obstacle in several supplied trades is an opposing 5m/15m
order block or FVG.  This candidate adds those active decision areas to the
source-span structure choices without changing family routing.  Only zones
observed before the interaction are eligible; the edge price first encountered
is the full target.  A cramped target is rejected by the existing 1R geometry
contract and is never replaced with a farther extension.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioSetup, StructureFamily, StructureZone
from domain import Candle, Side
from easychart_re1_geometry_v2 import (
    EasyChartRE1GeometryV2Bundle,
    GeometryV2HorizontalScenarioEngine,
    GeometryV2MajorLiquidityScenarioEngine,
    GeometryV2NaturalScenarioEngine,
    TargetChoice,
)
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneKind, ZoneSide


DECISION_AREA_OBJECTIVE_V2_RULE = (
    "SOURCE_EXPLICIT:"
    "FULL_TARGET_MAY_BE_THE_FIRST_PREEXISTING_ACTIVE_OPPOSING_FIVE_OR_FIFTEEN_MINUTE_HIGH_QUALITY_OB_OR_FVG_EDGE"
)
if DECISION_AREA_OBJECTIVE_V2_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (DECISION_AREA_OBJECTIVE_V2_RULE,)


class DecisionAreaObjectiveV2Mixin:
    """Add robust active decision areas to already meaningful target choices."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.decision_target_zones = EasyChartZoneDetector(
            self.symbol,
            self.decision_minutes,
            self.tick_size,
        )
        self.higher_target_zones = EasyChartZoneDetector(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )
        self._decision_target_counts: dict[str, int] = {}

    def _target_inc(self, key: str) -> None:
        self._decision_target_counts[key] = self._decision_target_counts.get(key, 0) + 1

    @staticmethod
    def _qualified(zone: PriceZone) -> bool:
        return zone.kind is ZoneKind.FVG or bool(
            zone.kind is ZoneKind.ORDER_BLOCK
            and getattr(zone, "high_quality_by_size", False)
        )

    def _snapshot(self, zone: PriceZone, time_ns: int) -> StructureZone:
        kind = (
            ObjectKind.HORIZONTAL_RESISTANCE
            if zone.side is ZoneSide.RESISTANCE
            else ObjectKind.HORIZONTAL_SUPPORT
        )
        formation_indices = tuple(
            getattr(zone, "formation_indices", (zone.formed_index,)),
        )
        return StructureZone(
            zone_id=f"TARGET_DECISION_AREA:{zone.zone_id}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=zone.side,
            timeframe_minutes=zone.timeframe_minutes,
            lower=zone.lower,
            upper=zone.upper,
            invalidation=zone.invalidation,
            impulse_extreme=float(getattr(zone, "impulse_extreme", zone.invalidation)),
            formed_index=zone.formed_index,
            formed_time_ns=zone.formed_time_ns,
            observed_time_ns=zone.observed_time_ns,
            formation_indices=formation_indices,
            strength_ratio=float(getattr(zone, "strength_ratio", 1.0)),
            source_structure_id=f"TARGET_DECISION_AREA:{zone.zone_id}",
            source_pivot_span=2,
            first_touch_time_ns=getattr(zone, "first_touch_time_ns", None),
            consumed=bool(getattr(zone, "consumed", False)),
        )

    def _zone_choice(
        self,
        detector: EasyChartZoneDetector,
        side: Side,
        bar: Candle,
        source: str,
    ) -> tuple[str, TargetChoice] | None:
        wanted = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        candidates = [
            zone
            for zone in detector.active_zones(side=wanted)
            if self._qualified(zone)
            and zone.observed_time_ns < bar.ts_close_ns
            and (
                (side is Side.LONG and zone.lower > bar.high)
                or (side is Side.SHORT and zone.upper < bar.low)
            )
        ]
        if not candidates:
            return None
        selected = (
            min(
                candidates,
                key=lambda item: (
                    item.lower,
                    -float(getattr(item, "strength_ratio", 1.0)),
                    item.zone_id,
                ),
            )
            if side is Side.LONG
            else max(
                candidates,
                key=lambda item: (
                    item.upper,
                    float(getattr(item, "strength_ratio", 1.0)),
                    item.zone_id,
                ),
            )
        )
        price = selected.lower if side is Side.LONG else selected.upper
        return source, (self._snapshot(selected, bar.ts_close_ns), price, None, None)

    def _select_target(self, context, side, path, bar):  # type: ignore[no-untyped-def]
        structural = super()._select_target(context, side, path, bar)
        choices: list[tuple[str, TargetChoice]] = []
        if structural is not None:
            choices.append(("MEANINGFUL_STRUCTURE", structural))
        for value in (
            self._zone_choice(self.decision_target_zones, side, bar, "5M_DECISION_AREA"),
            self._zone_choice(self.higher_target_zones, side, bar, "15M_DECISION_AREA"),
        ):
            if value is not None:
                self._append_unique(choices, value[0], value[1])
        if not choices:
            return None
        source, selected = self._nearest(side, choices)
        self._target_inc(f"selected_{source.lower()}")
        self._trace(
            "decision_area_objective_v2_selected",
            bar.ts_close_ns,
            side=side.name,
            selected_source=source,
            selected_zone_id=selected[0].zone_id,
            selected_price=selected[1],
            rule_provenance=DECISION_AREA_OBJECTIVE_V2_RULE,
        )
        return selected

    def _target_is_spent(self, setup: ScenarioSetup, bar: Candle) -> bool:
        target = setup.target_zone
        if target is not None and target.source_structure_id.startswith("TARGET_DECISION_AREA:"):
            if setup.target_price is None:
                return True
            touched = (
                bar.high >= setup.target_price
                if setup.side is Side.LONG
                else bar.low <= setup.target_price
            )
            return touched and bar.ts_close_ns > setup.interaction_time_ns
        return super()._target_is_spent(setup, bar)

    def on_bar(self, timeframe_minutes: int, bar: Candle):  # type: ignore[no-untyped-def]
        if timeframe_minutes == self.higher_minutes:
            self.higher_target_zones.on_bar(bar)
        if timeframe_minutes == self.decision_minutes:
            self.decision_target_zones.on_bar(bar)
        return super().on_bar(timeframe_minutes, bar)

    @property
    def decision_area_objective_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._decision_target_counts.items())),
            "decision_zones": dict(self.decision_target_zones.diagnostics),
            "higher_zones": dict(self.higher_target_zones.diagnostics),
            "rule_provenance": DECISION_AREA_OBJECTIVE_V2_RULE,
        }


class ZoneTargetV2NaturalScenarioEngine(
    DecisionAreaObjectiveV2Mixin,
    GeometryV2NaturalScenarioEngine,
):
    pass


class ZoneTargetV2HorizontalScenarioEngine(
    DecisionAreaObjectiveV2Mixin,
    GeometryV2HorizontalScenarioEngine,
):
    pass


class ZoneTargetV2MajorLiquidityScenarioEngine(
    DecisionAreaObjectiveV2Mixin,
    GeometryV2MajorLiquidityScenarioEngine,
):
    pass


class EasyChartRE1ZoneTargetV2Bundle(EasyChartRE1GeometryV2Bundle):
    """Complete router with natural structure and decision-area objectives."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ZoneTargetV2NaturalScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = ZoneTargetV2HorizontalScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.liquidity = ZoneTargetV2MajorLiquidityScenarioEngine(
            symbol,
            tick_size,
            scale_name="MAJOR_LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_flip = ZoneTargetV2HorizontalScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        for key in ("micro", "horizontal", "liquidity", "horizontal_flip"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["decision_area_objectives_v2"] = {
            "micro": self.micro.decision_area_objective_diagnostics,
            "horizontal": self.horizontal.decision_area_objective_diagnostics,
            "major_liquidity": self.liquidity.decision_area_objective_diagnostics,
            "horizontal_flip": self.horizontal_flip.decision_area_objective_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ZoneTargetV2Bundle
