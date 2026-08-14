"""Natural pre-entry geometry for the coherent EasyChart RE1 policy.

A one-minute footprint refines entry; it does not define the whole thesis.
Stops therefore include the latest confirmed five-minute counter swing formed
inside the decision episode, and the full target is the first causal 5m/15m
obstacle, exact channel edge, or first extension midline. FVG confirmation may
use only a still-valid overlapping OB formed by the same structure event.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_coherent import (
    CoherentObjectiveKind,
    EasyChartRE1CoherentBundle,
    FirstExtensionMidlineScenarioEngine,
)
from easychart_re1_confirmed import ConfirmedRepeatedDefenseScenarioEngine
from easychart_re1_major_swing import MajorSwingLiquidityScenarioEngine
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from scenario_context_v5 import ScenarioContextMixin
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


NATURAL_GEOMETRY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FULL_PLAN_USES_LATEST_CONFIRMED_FIVE_MINUTE_COUNTER_SWING_AND_FIRST_FIVE_OR_FIFTEEN_MINUTE_OBSTACLE"
)
EPISODE_LOCAL_FVG_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FVG_CONFIRMATION_USES_ONLY_A_STILL_VALID_OVERLAPPING_ORDER_BLOCK_FORMED_AFTER_THE_CURRENT_STRUCTURE_EVENT"
)
for _rule in (NATURAL_GEOMETRY_RULE, EPISODE_LOCAL_FVG_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


TargetChoice = tuple[StructureZone, float, str | None, float | None]


class EpisodeLocalFVGMixin:
    @staticmethod
    def _invalidated(zone: PriceZone, bar: Candle) -> bool:
        return bar.low <= zone.invalidation if zone.side is ZoneSide.SUPPORT else bar.high >= zone.invalidation

    def _survived(self, zone: PriceZone, end_index: int) -> bool:
        bars = self.trigger_detector.bars
        if end_index >= len(bars):
            raise RuntimeError("FVG observation exceeds trigger history")
        return not any(self._invalidated(zone, bars[i]) for i in range(zone.formed_index + 1, end_index + 1))

    def _overlapping_context_order_blocks(self, fvg: PriceZone, setup: ScenarioSetup) -> list[PriceZone]:
        event_start = setup.confirmation_time_ns or setup.interaction_time_ns
        candidates = [
            zone
            for zone in self.trigger_detector.zones
            if zone.kind is ZoneKind.ORDER_BLOCK
            and zone.side is fvg.side
            and event_start < zone.observed_time_ns <= fvg.observed_time_ns
            and zone.formed_index <= fvg.formed_index
            and zone.overlaps(fvg)
            and self._formation_touches_context(zone, setup)
        ]
        output = [zone for zone in candidates if self._survived(zone, fvg.formed_index)]
        if output:
            self._inc("event_local_fvg_same_episode_order_block_confirmed")
            self._trace(
                "event_local_fvg_same_episode_order_block_confirmed",
                fvg.observed_time_ns,
                setup,
                fvg_zone_id=fvg.zone_id,
                order_block_zone_ids=[zone.zone_id for zone in output],
                event_start_time_ns=event_start,
                rule_provenance=EPISODE_LOCAL_FVG_RULE,
            )
        elif candidates:
            self._inc("event_local_fvg_order_blocks_invalidated_before_fvg")
        return output


class NaturalGeometryMixin:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.decision_structure = NearestAnyPivotStructureBook(self.symbol, self.decision_minutes, self.tick_size)
        self._geometry_counts: dict[str, int] = {}

    def _ginc(self, key: str) -> None:
        self._geometry_counts[key] = self._geometry_counts.get(key, 0) + 1

    @staticmethod
    def _choice(value: tuple[StructureZone, float] | None) -> TargetChoice | None:
        return None if value is None else (value[0], value[1], None, None)

    def _append(self, choices: list[tuple[str, TargetChoice]], source: str, value: TargetChoice | None) -> None:
        if value is None:
            return
        zone, price, _, _ = value
        if any(
            existing[0].source_structure_id == zone.source_structure_id
            or abs(existing[1] - price) <= self.tick_size * 0.5
            for _, existing in choices
        ):
            return
        choices.append((source, value))

    @staticmethod
    def _nearest(side: Side, choices: list[tuple[str, TargetChoice]]) -> tuple[str, TargetChoice]:
        if side is Side.LONG:
            return min(enumerate(choices), key=lambda x: (x[1][1][1], x[0]))[1]
        return max(enumerate(choices), key=lambda x: (x[1][1][1], -x[0]))[1]

    def _midpoint(self, context: StructureZone, side: Side, path: ScenarioPath, bar: Candle) -> TargetChoice | None:
        if path is not ScenarioPath.ACCEPTANCE or context.family is not StructureFamily.CHANNEL:
            return None
        channel = self.structure.channel_for_boundary(context.source_structure_id)
        extension_at = getattr(self, "_channel_extension_at", None)
        if channel is None or extension_at is None:
            return None
        zone, price = extension_at(channel, side, bar.ts_close_ns)
        return zone, price, channel.channel_id, channel.mid_at(bar.ts_close_ns)

    def _select_target(self, context: StructureZone, side: Side, path: ScenarioPath, bar: Candle) -> TargetChoice | None:
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
        self._append(choices, "CONTEXT", contextual)
        self._append(choices, "EXTENSION_MIDLINE", self._midpoint(context, side, path, bar))
        self._append(choices, "15M_STRUCTURE", self._choice(higher))
        self._append(choices, "5M_STRUCTURE", self._choice(decision))
        if not choices:
            self._ginc("no_meaningful_objective")
            return None
        source, selected = self._nearest(side, choices)
        self._ginc(f"objective_{source.lower()}")
        self._trace(
            "natural_daytrade_objective_selected",
            bar.ts_close_ns,
            side=side.name,
            path=path.value,
            context_zone_id=context.zone_id,
            selected_source=source,
            selected_zone_id=selected[0].zone_id,
            selected_price=selected[1],
            candidates=[{"source": s, "zone_id": v[0].zone_id, "price": v[1]} for s, v in choices],
            rule_provenance=NATURAL_GEOMETRY_RULE,
        )
        return selected

    def _channel_target_at(self, setup: ScenarioSetup, time_ns: int) -> tuple[StructureZone, float] | None:
        target = setup.target_zone
        if target is None or target.family is not StructureFamily.CHANNEL:
            return None
        if target.kind is CoherentObjectiveKind.FIRST_EXTENSION_MIDLINE:
            if setup.channel_id is None:
                return None
            channel = self.structure.channel_by_id(setup.channel_id)
            return None if channel is None else self._channel_extension_at(channel, setup.side, time_ns)
        edge = target.source_structure_id.rsplit(":", 1)[-1]
        if edge not in {"LOWER", "UPPER"}:
            return None
        channel = self.structure.channel_for_boundary(target.source_structure_id)
        if channel is None:
            return None
        zone = self.structure.channel_edge_snapshot(channel, edge, time_ns)
        return zone, channel.lower_at(time_ns) if edge == "LOWER" else channel.upper_at(time_ns)

    def _decision_stop(self, setup: ScenarioSetup, bar: Candle, micro_stop: float) -> float:
        wanted = "LOW" if setup.side is Side.LONG else "HIGH"
        pivots = [
            pivot
            for pivot in self.decision_structure.pivots
            if pivot.side == wanted
            and pivot.observed_time_ns <= bar.ts_close_ns
            and pivot.event_time_ns >= setup.interaction_time_ns
            and ((setup.side is Side.LONG and pivot.price < bar.close) or (setup.side is Side.SHORT and pivot.price > bar.close))
        ]
        pivot = max(pivots, key=lambda p: (p.event_time_ns, p.observed_time_ns, p.span, p.pivot_id), default=None)
        if pivot is None:
            self._ginc("micro_stop_retained_no_decision_swing")
            return micro_stop
        structural = pivot.price - self.tick_size if setup.side is Side.LONG else pivot.price + self.tick_size
        stop = min(micro_stop, structural) if setup.side is Side.LONG else max(micro_stop, structural)
        if stop != micro_stop:
            self._ginc("stop_expanded_to_decision_swing")
            self._trace(
                "decision_swing_invalidation_selected",
                bar.ts_close_ns,
                setup,
                micro_stop=micro_stop,
                structural_stop=stop,
                pivot_id=pivot.pivot_id,
                pivot_price=pivot.price,
                rule_provenance=NATURAL_GEOMETRY_RULE,
            )
        else:
            self._ginc("micro_stop_already_structural")
        return stop

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
    ) -> V5TradePlan | None:
        return super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=self._decision_stop(setup, bar, stop),
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes != self.decision_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self.decision_structure.on_bar(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        self.decision_structure.observe_price(bar)
        return plans

    @property
    def natural_geometry_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._geometry_counts.items())),
            "decision_structure": dict(self.decision_structure.diagnostics),
            "rule_provenance": NATURAL_GEOMETRY_RULE,
        }


class NaturalMicroEngine(NaturalGeometryMixin, EpisodeLocalFVGMixin, FirstExtensionMidlineScenarioEngine):
    pass


class NaturalHorizontalEngine(NaturalGeometryMixin, EpisodeLocalFVGMixin, ConfirmedRepeatedDefenseScenarioEngine):
    pass


class NaturalMajorSwingEngine(NaturalGeometryMixin, EpisodeLocalFVGMixin, MajorSwingLiquidityScenarioEngine):
    pass


class EasyChartRE1NaturalGeometryBundle(EasyChartRE1CoherentBundle):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = NaturalMicroEngine(symbol, tick_size, scale_name="MICRO", higher_minutes=15, decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr)
        self.horizontal = NaturalHorizontalEngine(symbol, tick_size, scale_name="HORIZONTAL", higher_minutes=15, decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr)
        self.major_swing = NaturalMajorSwingEngine(symbol, tick_size, scale_name="LIQUIDITY", higher_minutes=15, decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr)
        for key in ("micro", "horizontal", "major_swing"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["natural_daytrade_geometry_policy"] = {
            "micro": self.micro.natural_geometry_diagnostics,
            "horizontal": self.horizontal.natural_geometry_diagnostics,
            "major_swing": self.major_swing.natural_geometry_diagnostics,
            "rule_provenance": NATURAL_GEOMETRY_RULE,
        }
        output["episode_local_fvg_policy"] = {"rule_provenance": EPISODE_LOCAL_FVG_RULE}
        return output


MultiScaleScenarioBundle = EasyChartRE1NaturalGeometryBundle
