"""Trend-line break, first retest and order-block execution.

This is repeatedly demonstrated in the supplied trading walkthroughs:

    confirmed wick trend line
    -> break in the reversal direction
    -> first retest
    -> same-direction order block at the retest
    -> stop at that order block's wick invalidation
    -> nearest pre-existing opposing structure.

A downtrend-line break can create only a long plan; an uptrend-line break can
create only a short plan.  The order block must be observable after the break,
its formation must interact with the projected trend line, and the completed
first retest must not already have crossed its invalidation.  No channel,
horizontal, generic bounce, FVG-only or post-entry management rule is added.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioPath, ScenarioSetup, SetupState, StructureFamily
from diagonal_core_v20 import DiagonalCoreScenarioEngine, MicroDiagonalCoreBundleV20
from domain import Side
from easychart_zones import PriceZone, ZoneKind, ZoneSide


TRENDLINE_OB_RULE = (
    "SOURCE_EXPLICIT:TRENDLINE_BREAK_FIRST_RETEST_WITH_SAME_DIRECTION_ORDER_BLOCK_ENTRY"
)
TRENDLINE_OB_STOP_RULE = (
    "SOURCE_EXPLICIT:ORDER_BLOCK_ENTRY_STOPS_AT_ORDER_BLOCK_FORMATION_WICK_INVALIDATION"
)
for _rule in (TRENDLINE_OB_RULE, TRENDLINE_OB_STOP_RULE):
    if _rule not in _contracts.SOURCE_RULES:
        _contracts.SOURCE_RULES += (_rule,)


VALID_BREAKS = {
    (ObjectKind.DOWNTREND_LINE, Side.LONG),
    (ObjectKind.UPTREND_LINE, Side.SHORT),
}


class TrendlineOrderBlockScenarioEngine(DiagonalCoreScenarioEngine):
    """Only source-demonstrated trend-line reversals with OB execution."""

    def _create_setup(
        self,
        *,
        path: ScenarioPath,
        context: Any,
        members: tuple[Any, ...],
        bar: Any,
        decision_index: int,
        state: SetupState,
    ) -> ScenarioSetup | None:
        if path is not ScenarioPath.ACCEPTANCE:
            return None
        if any(member.family is StructureFamily.CHANNEL for member in members):
            return None
        side = Side.SHORT if context.side is ZoneSide.SUPPORT else Side.LONG
        if not any((member.kind, side) in VALID_BREAKS for member in members):
            return None
        return super()._create_setup(
            path=path,
            context=context,
            members=members,
            bar=bar,
            decision_index=decision_index,
            state=state,
        )

    def _retest_order_block(self, setup: ScenarioSetup, bar: Any) -> PriceZone | None:
        wanted = ZoneSide.SUPPORT if setup.side is Side.LONG else ZoneSide.RESISTANCE
        confirmation = setup.confirmation_time_ns or 0
        candidates: list[PriceZone] = []
        for zone in self.trigger_detector.zones:
            if zone.kind is not ZoneKind.ORDER_BLOCK or zone.side is not wanted:
                continue
            if not confirmation < zone.observed_time_ns <= bar.ts_close_ns:
                continue
            if not self._formation_touches_context(zone, setup):
                continue
            if not (bar.low <= zone.upper and bar.high >= zone.lower):
                continue
            invalidated = (
                bar.low <= zone.invalidation
                if setup.side is Side.LONG
                else bar.high >= zone.invalidation
            )
            if invalidated:
                continue
            candidates.append(zone)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda zone: (
                zone.observed_time_ns,
                zone.upper - zone.lower,
                zone.zone_id,
            ),
        )

    def _make_plan(
        self,
        setup: ScenarioSetup,
        bar: Any,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ):
        del stop, trigger_zone, trigger_kind, trigger_strength
        if setup.path is not ScenarioPath.ACCEPTANCE:
            return None
        order_block = self._retest_order_block(setup, bar)
        if order_block is None:
            self._finish(
                setup,
                SetupState.UNRESOLVED,
                bar.ts_close_ns,
                "trendline_first_retest_without_valid_order_block",
            )
            return None
        self._audit(order_block)
        ob_stop = order_block.invalidation
        plan = super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=ob_stop,
            trigger_zone=order_block,
            trigger_kind=order_block.kind,
            trigger_strength=order_block.strength_ratio,
        )
        if plan is not None:
            self._inc("trendline_ob_plan_created")
            self._trace(
                "trendline_ob_plan_created",
                bar.ts_close_ns,
                setup,
                plan_id=plan.plan_id,
                order_block_id=order_block.zone_id,
                order_block_invalidation=ob_stop,
                provenance=(TRENDLINE_OB_RULE, TRENDLINE_OB_STOP_RULE),
            )
        return plan


class MicroTrendlineOrderBlockBundleV26(MicroDiagonalCoreBundleV20):
    """Micro execution family, four-symbol account routing unchanged."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = TrendlineOrderBlockScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO_TRENDLINE_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["scenario_policy"] = {
            "name": "TRENDLINE_BREAK_FIRST_RETEST_ORDER_BLOCK",
            "rule_provenance": (TRENDLINE_OB_RULE, TRENDLINE_OB_STOP_RULE),
        }
        return output
