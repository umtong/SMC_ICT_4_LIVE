"""Mechanism-selective EasyChart RE1 routing.

The first-response accepted-break policy repaired the largest failing regime,
but the audit still exposed three machine-generalization errors:

* the current 60-minute countertrend exception treats a merely overlapping HTF
  area as if it were a completed reversal episode;
* diagonal channel rotations have not produced a repeatable opportunity family;
* an event-local FVG is accepted as a trigger even when no order block overlaps
  the imbalance.  In the supplied material FVG alone is explicitly discouraged,
  while OB/FVG overlap at structure is the stronger footprint interpretation.

This module does not tune numeric thresholds.  It assigns each mechanism one
well-defined responsibility:

* established 60-minute direction routes executable plans; countertrend is
  deferred until a dedicated HTF sweep/reclaim reversal family exists;
* accepted breaks retain the immediate first-response confirmation;
* diagonal rotation is retained in diagnostics but not sent to the account;
* an order block may confirm a rejection directly; an FVG may confirm only when
  it overlaps a still-valid same-side order block whose formation also belongs
  to the same structure interaction.

The horizontal repeated-defense sweep family remains independent.  Entry,
initial stop, structural target, cost reserves, risk sizing, one global account
slot and post-entry structural protection are unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, V5TradePlan
from domain import Candle
from easychart_re1_horizontal import RepeatedDefenseScenarioEngine
from easychart_re1_reaction import (
    EasyChartRE1ReactionBundle,
    FirstResponseAcceptanceScenarioEngine,
)
from easychart_zones import PriceZone, ZoneKind


COUNTERTREND_DEFERRED_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "COUNTERTREND_IS_NOT_EXECUTABLE_UNTIL_A_DEDICATED_HTF_SWEEP_RECLAIM_REVERSAL_EPISODE_EXISTS"
)
ROTATION_DEFERRED_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CHANNEL_ROTATION_REMAINS_DIAGNOSTIC_UNTIL_ITS_ENTRY_RESPONSE_IS_REBUILT"
)
FVG_OB_CONFLUENCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "EVENT_LOCAL_FVG_TRIGGER_REQUIRES_OVERLAPPING_ACTIVE_SAME_SIDE_ORDER_BLOCK_AT_THE_SAME_STRUCTURE_EPISODE"
)
for _rule in (COUNTERTREND_DEFERRED_RULE, ROTATION_DEFERRED_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)
if FVG_OB_CONFLUENCE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FVG_OB_CONFLUENCE_RULE,)


class FVGOrderBlockConfluenceMixin:
    """Use FVG as an execution footprint only when an active OB overlaps it."""

    def _overlapping_context_order_blocks(
        self,
        fvg: PriceZone,
        setup: ScenarioSetup,
    ) -> list[PriceZone]:
        return [
            zone
            for zone in self.trigger_detector.active_zones(
                side=fvg.side,
                kind=ZoneKind.ORDER_BLOCK,
            )
            if zone.observed_time_ns <= fvg.observed_time_ns
            and zone.overlaps(fvg)
            and self._formation_touches_context(zone, setup)
        ]

    def _select_footprint(
        self,
        candidates: list[PriceZone],
        setup: ScenarioSetup,
    ) -> PriceZone | None:
        # An OB is the executable footprint when one was created by this
        # displacement.  This preserves the inherited deterministic ordering
        # among multiple OB candidates.
        order_blocks = [zone for zone in candidates if zone.kind is ZoneKind.ORDER_BLOCK]
        if order_blocks:
            return super()._select_footprint(order_blocks, setup)

        qualified_fvgs: list[PriceZone] = []
        for zone in candidates:
            if zone.kind is not ZoneKind.FVG:
                continue
            overlaps = self._overlapping_context_order_blocks(zone, setup)
            if overlaps:
                qualified_fvgs.append(zone)
                current = self._current_trigger_bar
                self._inc("event_local_fvg_with_overlapping_order_block")
                self._trace(
                    "event_local_fvg_with_overlapping_order_block",
                    zone.observed_time_ns if current is None else current.ts_close_ns,
                    setup,
                    fvg_zone_id=zone.zone_id,
                    order_block_zone_ids=[item.zone_id for item in overlaps],
                    rule_provenance=FVG_OB_CONFLUENCE_RULE,
                )
            else:
                current = self._current_trigger_bar
                self._inc("event_local_fvg_without_order_block_deferred")
                self._trace(
                    "event_local_fvg_without_order_block_deferred",
                    zone.observed_time_ns if current is None else current.ts_close_ns,
                    setup,
                    fvg_zone_id=zone.zone_id,
                    rule_provenance=FVG_OB_CONFLUENCE_RULE,
                )
        return super()._select_footprint(qualified_fvgs, setup)


class SelectiveFirstResponseScenarioEngine(
    FVGOrderBlockConfluenceMixin,
    FirstResponseAcceptanceScenarioEngine,
):
    """Accepted-break response plus confluence-aware rejection footprints."""


class SelectiveRepeatedDefenseScenarioEngine(
    FVGOrderBlockConfluenceMixin,
    RepeatedDefenseScenarioEngine,
):
    """Horizontal sweep/reclaim with the same footprint semantics."""


class EasyChartRE1SelectiveBundle(EasyChartRE1ReactionBundle):
    """One account stream containing only currently demonstrated mechanisms."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = SelectiveFirstResponseScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = SelectiveRepeatedDefenseScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0
        self._audit_offsets["horizontal"] = 0

    def _route_plan(self, plan: V5TradePlan) -> bool:
        allowed = super()._route_plan(plan)
        if not allowed:
            return False

        if plan.scenario_path == ScenarioPath.ROTATION.value:
            self._router_inc("context_router_deferred_rotation_family")
            self._bundle_trace.append(
                {
                    "scenario_kind": "context_router_deferred_rotation_family",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "scenario_path": plan.scenario_path,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "rule_provenance": ROTATION_DEFERRED_RULE,
                },
            )
            return False

        if self._macro_side is not None and plan.side is not self._macro_side:
            self._router_inc("context_router_deferred_countertrend_family")
            self._bundle_trace.append(
                {
                    "scenario_kind": "context_router_deferred_countertrend_family",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": self._macro_side.name,
                    "scenario_path": plan.scenario_path,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "rule_provenance": COUNTERTREND_DEFERRED_RULE,
                },
            )
            return False
        return True

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["mechanism_selective_policy"] = {
            "countertrend_executable": False,
            "rotation_executable": False,
            "order_block_trigger_executable": True,
            "fvg_trigger_requires_order_block_overlap": True,
            "rules": (
                COUNTERTREND_DEFERRED_RULE,
                ROTATION_DEFERRED_RULE,
                FVG_OB_CONFLUENCE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1SelectiveBundle
