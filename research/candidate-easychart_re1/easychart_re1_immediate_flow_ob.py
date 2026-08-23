"""Immediate formation-close entry for contextual, flow-validated 15m OBs.

The supplied ETH live trade acts when the 15-minute bullish engulfing candle
closes at a pre-existing lower structure.  The order block itself is the
confirmation: entry is the completed engulfing close, invalidation is below all
formation wicks, and the first opposing structure is the objective.  Requiring a
new one-minute footprint after that close changes the scenario and often enters
far later than the demonstrated trade.

This module adds that exact responsibility as an independent family:

* the high-quality 15-minute engulfing body must be flow-valid;
* at least one source/impulse formation candle must touch a pre-existing
  same-side 15-minute wick structure, trend line or channel boundary;
* the full-position plan is fixed at the engulfing close;
* stop is beyond the complete formation wick and target is the first causal
  5/15-minute obstacle;
* the existing delayed first-return OB family remains available, but a direct
  formation entry claims the same later episode first.

No clock, ATR, score, session, symbol or outcome-dependent rule is introduced.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, SetupState, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow_ob import (
    FlowValidatedDecisionAreaEngine,
    FlowValidatedOrderBlockDecisionStructureBook,
)
from easychart_re1_flow_ob_responsibility import EasyChartRE1ResponsibleFlowOBBundle
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


IMMEDIATE_CONTEXT_FLOW_OB_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_FLOW_VALIDATED_FIFTEEN_MINUTE_ENGULFING_OB_FORMED_AT_PREEXISTING_STRUCTURE_MAY_ENTER_ON_ITS_COMPLETED_CLOSE_WITH_ALL_FORMATION_WICKS_AS_INVALIDATION"
)
if IMMEDIATE_CONTEXT_FLOW_OB_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (IMMEDIATE_CONTEXT_FLOW_OB_RULE,)


class ContextualFlowValidatedOBBook(FlowValidatedOrderBlockDecisionStructureBook):
    """Register only the flow-valid OBs which are born at known structure."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.location_evidence: dict[str, tuple[str, ...]] = {}
        self._location_counts: dict[str, int] = {}

    def _linc(self, key: str) -> None:
        self._location_counts[key] = self._location_counts.get(key, 0) + 1

    def _preexisting_location_ids(self, zone: PriceZone) -> tuple[str, ...]:
        formation = tuple(zone.formation_indices)
        if not formation:
            return ()
        wanted = ZoneSide.SUPPORT if zone.side is ZoneSide.SUPPORT else ZoneSide.RESISTANCE
        output: set[str] = set()
        for index in formation:
            if not 0 <= index < len(self.bars):
                continue
            bar = self.bars[index]
            # Call the inherited causal structure view directly.  The decision-
            # OB book's public boundaries_at intentionally exposes only OB
            # decision areas, while location evidence also includes wick
            # pivots, trend lines and channel edges.
            boundaries = NearestAnyPivotStructureBook.boundaries_at(self, bar.ts_close_ns)
            for context in boundaries:
                if context.side is not wanted:
                    continue
                if context.observed_time_ns >= zone.formed_time_ns:
                    continue
                if bar.low <= context.upper and bar.high >= context.lower:
                    output.add(context.source_structure_id)
        return tuple(sorted(output))

    def _register(self, zone: PriceZone) -> None:
        if (
            zone.kind is not ZoneKind.ORDER_BLOCK
            or not zone.high_quality_by_size
            or zone.zone_id in self._source_ids
        ):
            return
        locations = self._preexisting_location_ids(zone)
        if not locations:
            self._source_ids.add(zone.zone_id)
            self._linc("formation_ob_not_at_preexisting_same_side_structure")
            return
        super()._register(zone)
        level_id = f"DECISION_OB:{zone.zone_id}"
        if level_id not in self.flow_evidence:
            self._linc("contextual_ob_rejected_by_formation_flow")
            return
        self.location_evidence[level_id] = locations
        self._linc("contextual_flow_ob_registered")

    @property
    def contextual_validation_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._location_counts.items())),
            "validated_levels": len(self.location_evidence),
            "formation_flow": self.flow_validation_diagnostics,
            "rule_provenance": IMMEDIATE_CONTEXT_FLOW_OB_RULE,
        }


class ImmediateContextFlowOBEngine(FlowValidatedDecisionAreaEngine):
    """Emit the source-like plan at the validated 15m engulfing close."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = ContextualFlowValidatedOBBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )
        self._level_offset = 0
        self._immediate_counts: dict[str, int] = {}
        self._immediate_trace: list[dict[str, Any]] = []

    def _iinc(self, key: str) -> None:
        self._immediate_counts[key] = self._immediate_counts.get(key, 0) + 1

    def _new_contexts(self, time_ns: int) -> list[StructureZone]:
        levels = self.structure.levels[self._level_offset :]
        self._level_offset = len(self.structure.levels)
        output: list[StructureZone] = []
        for level in levels:
            if level.observed_time_ns != time_ns:
                continue
            if level.level_id not in self.structure.location_evidence:
                continue
            output.append(self.structure._snapshot(level, time_ns))
        return output

    def _immediate_plan(self, context: StructureZone, bar: Candle) -> V5TradePlan | None:
        side = Side.LONG if context.side is ZoneSide.SUPPORT else Side.SHORT
        setup = self._create_setup(
            path=ScenarioPath.BOUNCE,
            context=context,
            members=(context,),
            bar=bar,
            decision_index=len(self.decision_bars),
            state=SetupState.WAITING_DISPLACEMENT,
        )
        if setup is None:
            self._iinc("immediate_context_ob_missing_objective")
            return None
        stop = context.invalidation
        plan = self._make_plan(
            setup,
            bar,
            entry=bar.close,
            stop=stop,
            trigger_zone=context,
            trigger_kind=ZoneKind.ORDER_BLOCK,
            trigger_strength=context.strength_ratio,
        )
        if plan is None:
            self._iinc("immediate_context_ob_geometry_rejected")
            return None
        family = "IMMEDIATE_CONTEXT_FLOW_OB_FORMATION_CLOSE"
        plan = replace(
            plan,
            causal_event_id=f"{family}:{setup.setup_id}",
            family=family,
            trigger_zone_kind=ZoneKind.ORDER_BLOCK.value,
            scale_name="IMMEDIATE_FLOW_OB",
            decision_timeframe_minutes=self.higher_minutes,
            trigger_timeframe_minutes=self.higher_minutes,
        )
        # Replace the stored generic object so diagnostics and bundle properties
        # expose exactly the executable plan which was routed.
        self.plans[-1] = plan
        self._iinc("immediate_context_flow_ob_plan_created")
        self._immediate_trace.append(
            {
                "scenario_kind": "immediate_context_flow_ob_plan_created",
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "plan_id": plan.plan_id,
                "side": side.name,
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "gross_rr": plan.gross_rr,
                "context_zone_id": context.zone_id,
                "location_ids": self.structure.location_evidence.get(
                    context.source_structure_id,
                    (),
                ),
                "rule_provenance": IMMEDIATE_CONTEXT_FLOW_OB_RULE,
            }
        )
        return plan

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        existing = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes != self.higher_minutes:
            return existing
        immediate = [
            plan
            for context in self._new_contexts(bar.ts_close_ns)
            if (plan := self._immediate_plan(context, bar)) is not None
        ]
        return immediate + existing

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self._immediate_trace
        self._immediate_trace = []
        return output

    @property
    def immediate_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._immediate_counts.items())),
            "validation": self.structure.contextual_validation_diagnostics,
            "rule_provenance": IMMEDIATE_CONTEXT_FLOW_OB_RULE,
        }


class EasyChartRE1ImmediateFlowOBBundle(EasyChartRE1ResponsibleFlowOBBundle):
    """Responsible core plus immediate source-like contextual 15m OB entries."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.immediate_flow_ob = ImmediateContextFlowOBEngine(
            symbol,
            tick_size,
            scale_name="IMMEDIATE_FLOW_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["immediate_flow_ob"] = 0
        self._immediate_bundle_counts: dict[str, int] = {}
        self._immediate_bundle_trace: list[dict[str, Any]] = []

    def _binc(self, key: str) -> None:
        self._immediate_bundle_counts[key] = self._immediate_bundle_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.immediate_flow_ob.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.immediate_flow_ob.plans

    def _route_immediate(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._binc("immediate_flow_ob_overlapped_existing_episode")
                continue
            if not self._route_plan(plan):
                self._binc("immediate_flow_ob_rejected_by_macro_context")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._binc("immediate_flow_ob_plan_allowed")
            self._immediate_bundle_trace.append(
                {
                    "scenario_kind": "immediate_context_flow_ob_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": IMMEDIATE_CONTEXT_FLOW_OB_RULE,
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        immediate: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw = self.immediate_flow_ob.on_bar(timeframe_minutes, bar)
            self._sync_audit("immediate_flow_ob", self.immediate_flow_ob)
            immediate = self._route_immediate(raw)
        routed = super().on_bar(timeframe_minutes, bar)
        return sorted(
            immediate + routed,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.immediate_flow_ob.drain_trace()
            + self._immediate_bundle_trace
        )
        self._immediate_bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.immediate_flow_ob.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["immediate_context_flow_ob"] = {
            "counts": dict(sorted(self._immediate_bundle_counts.items())),
            "engine": self.immediate_flow_ob.immediate_diagnostics,
            "rule_provenance": IMMEDIATE_CONTEXT_FLOW_OB_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ImmediateFlowOBBundle
