"""Natural stop/target geometry over the proven complete family router.

This version deliberately reuses ``EasyChartRE1CompleteBundle`` for all family
routing, duplicate-episode suppression and current-local alignment.  It changes
only each family's geometry:

* the lower frame refines entry, while the latest confirmed five-minute counter
  swing formed during the decision episode remains part of invalidation;
* the target is the first source-span-compatible five/fifteen-minute opposing
  structure or the exact contextual channel edge;
* a selected channel edge cannot be replaced by a farther equal-width extension;
* cramped sub-1R geometry is rejected by the existing account contract rather
  than repaired with a remote objective or an artificially tight stop.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, StructureFamily, StructureZone
from domain import Candle, Side
from easychart_re1_adjacent import SourceCandleLocatedMixin
from easychart_re1_complete import EasyChartRE1CompleteBundle
from easychart_re1_confirmed import ConfirmedRepeatedDefenseScenarioEngine
from easychart_re1_liquidity import MajorLiquidityScenarioEngine
from easychart_re1_phase import PhaseConfirmedSelectiveScenarioEngine
from scenario_context_v5 import ScenarioContextMixin
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


MEANINGFUL_OBJECTIVE_V2_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FULL_TARGET_IS_FIRST_SOURCE_SPAN_COMPATIBLE_FIVE_OR_FIFTEEN_MINUTE_OPPOSING_STRUCTURE_OR_EXACT_CONTEXTUAL_CHANNEL_EDGE"
)
DECISION_SWING_INVALIDATION_V2_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "MICRO_ENTRY_RISK_REMAINS_BEYOND_THE_LATEST_CONFIRMED_FIVE_MINUTE_COUNTER_SWING_FORMED_DURING_THE_DECISION_EPISODE"
)
for _rule in (MEANINGFUL_OBJECTIVE_V2_RULE, DECISION_SWING_INVALIDATION_V2_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


TargetChoice = tuple[StructureZone, float, str | None, float | None]


class NaturalGeometryV2Mixin:
    """Joint source-span target and five-minute decision invalidation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.decision_structure = NearestAnyPivotStructureBook(
            self.symbol,
            self.decision_minutes,
            self.tick_size,
        )
        self._natural_geometry_counts: dict[str, int] = {}

    def _geometry_inc(self, key: str) -> None:
        self._natural_geometry_counts[key] = self._natural_geometry_counts.get(key, 0) + 1

    @staticmethod
    def _choice(value: tuple[StructureZone, float] | None) -> TargetChoice | None:
        if value is None:
            return None
        zone, price = value
        return zone, price, None, None

    def _append_unique(
        self,
        choices: list[tuple[str, TargetChoice]],
        source: str,
        value: TargetChoice | None,
    ) -> None:
        if value is None:
            return
        zone, price, _, _ = value
        for _, existing in choices:
            existing_zone, existing_price, _, _ = existing
            if (
                existing_zone.source_structure_id == zone.source_structure_id
                or abs(existing_price - price) <= self.tick_size * 0.5
            ):
                return
        choices.append((source, value))

    @staticmethod
    def _nearest(side: Side, choices: list[tuple[str, TargetChoice]]) -> tuple[str, TargetChoice]:
        if side is Side.LONG:
            return min(enumerate(choices), key=lambda item: (item[1][1][1], item[0]))[1]
        return max(enumerate(choices), key=lambda item: (item[1][1][1], -item[0]))[1]

    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Candle,
    ) -> TargetChoice | None:
        # Bypass equal-width extension policy.  The context method provides the
        # exact opposite edge for a channel rejection and a real pre-existing
        # opposing structure for an accepted break.
        contextual = ScenarioContextMixin._select_target(self, context, side, path, bar)
        higher = self.structure.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        decision = self.decision_structure.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        choices: list[tuple[str, TargetChoice]] = []
        self._append_unique(choices, "CONTEXTUAL_STRUCTURE", contextual)
        self._append_unique(choices, "15M_SOURCE_SPAN_STRUCTURE", self._choice(higher))
        self._append_unique(choices, "5M_SOURCE_SPAN_STRUCTURE", self._choice(decision))
        if not choices:
            self._geometry_inc("no_meaningful_objective")
            return None
        source, selected = self._nearest(side, choices)
        zone, price, _, _ = selected
        self._geometry_inc(f"objective_selected_{source.lower()}")
        self._trace(
            "natural_geometry_objective_selected",
            bar.ts_close_ns,
            side=side.name,
            path=path.value,
            context_zone_id=context.zone_id,
            source_span=context.source_pivot_span,
            selected_source=source,
            selected_zone_id=zone.zone_id,
            selected_price=price,
            rule_provenance=MEANINGFUL_OBJECTIVE_V2_RULE,
        )
        return selected

    def _channel_target_at(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> tuple[StructureZone, float] | None:
        target = setup.target_zone
        if target is None or target.family is not StructureFamily.CHANNEL:
            return None
        edge = target.source_structure_id.rsplit(":", 1)[-1]
        if edge not in {"LOWER", "UPPER"}:
            return None
        channel = self.structure.channel_for_boundary(target.source_structure_id)
        if channel is None:
            return None
        zone = self.structure.channel_edge_snapshot(channel, edge, time_ns)
        price = channel.lower_at(time_ns) if edge == "LOWER" else channel.upper_at(time_ns)
        return zone, price

    def _decision_swing_stop(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        micro_stop: float,
    ) -> tuple[float, str | None]:
        wanted = "LOW" if setup.side is Side.LONG else "HIGH"
        candidates = [
            pivot
            for pivot in self.decision_structure.pivots
            if pivot.side == wanted
            and pivot.observed_time_ns <= bar.ts_close_ns
            and pivot.event_time_ns >= setup.interaction_time_ns
            and (
                (setup.side is Side.LONG and pivot.price < bar.close)
                or (setup.side is Side.SHORT and pivot.price > bar.close)
            )
        ]
        pivot = max(
            candidates,
            key=lambda item: (item.event_time_ns, item.observed_time_ns, item.span, item.pivot_id),
            default=None,
        )
        if pivot is None:
            self._geometry_inc("micro_stop_retained_no_decision_swing")
            return micro_stop, None
        structural = (
            pivot.price - self.tick_size
            if setup.side is Side.LONG
            else pivot.price + self.tick_size
        )
        stop = min(micro_stop, structural) if setup.side is Side.LONG else max(micro_stop, structural)
        if stop == micro_stop:
            self._geometry_inc("micro_stop_already_beyond_decision_swing")
            return stop, pivot.pivot_id
        self._geometry_inc("stop_expanded_to_decision_swing")
        self._trace(
            "decision_swing_invalidation_selected",
            bar.ts_close_ns,
            setup,
            micro_stop=micro_stop,
            structural_stop=stop,
            pivot_id=pivot.pivot_id,
            pivot_price=pivot.price,
            pivot_span=pivot.span,
            rule_provenance=DECISION_SWING_INVALIDATION_V2_RULE,
        )
        return stop, pivot.pivot_id

    def _make_plan(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ):
        structural_stop, _ = self._decision_swing_stop(setup, bar, stop)
        return super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=structural_stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle):  # type: ignore[no-untyped-def]
        if timeframe_minutes != self.decision_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self.decision_structure.on_bar(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        self.decision_structure.observe_price(bar)
        return plans

    @property
    def natural_geometry_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._natural_geometry_counts.items())),
            "decision_structure": dict(self.decision_structure.diagnostics),
            "objective_rule": MEANINGFUL_OBJECTIVE_V2_RULE,
            "stop_rule": DECISION_SWING_INVALIDATION_V2_RULE,
        }


class GeometryV2NaturalScenarioEngine(
    NaturalGeometryV2Mixin,
    SourceCandleLocatedMixin,
    PhaseConfirmedSelectiveScenarioEngine,
):
    pass


class GeometryV2HorizontalScenarioEngine(
    NaturalGeometryV2Mixin,
    SourceCandleLocatedMixin,
    ConfirmedRepeatedDefenseScenarioEngine,
):
    pass


class GeometryV2MajorLiquidityScenarioEngine(
    NaturalGeometryV2Mixin,
    SourceCandleLocatedMixin,
    MajorLiquidityScenarioEngine,
):
    pass


class EasyChartRE1GeometryV2Bundle(EasyChartRE1CompleteBundle):
    """Complete proven router with repaired natural geometry in every family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = GeometryV2NaturalScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = GeometryV2HorizontalScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.liquidity = GeometryV2MajorLiquidityScenarioEngine(
            symbol,
            tick_size,
            scale_name="MAJOR_LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_flip = GeometryV2HorizontalScenarioEngine(
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
        output["natural_geometry_v2"] = {
            "micro": self.micro.natural_geometry_diagnostics,
            "horizontal": self.horizontal.natural_geometry_diagnostics,
            "major_liquidity": self.liquidity.natural_geometry_diagnostics,
            "horizontal_flip": self.horizontal_flip.natural_geometry_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1GeometryV2Bundle
