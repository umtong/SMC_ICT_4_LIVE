"""Flow-validated double-engulfing order-block opportunity family.

A double engulfing is not another threshold on an ordinary OB.  It is a complete
three-candle transfer of control: candle B engulfs A in one direction and candle
C then engulfs B in the opposite direction.  The supplied live material treats
the body of B as the final decision zone and all three wicks as invalidation,
especially when the sequence forms at pre-existing structure or liquidity.

This module adds that independent fifteen-minute family.  The sequence is
published only when:

* A/B and B/C are opposite-body engulfs;
* at least one formation candle touches a same-side structure already observable
  before A closes;
* C has aligned cumulative one-minute taker flow, net price progress and at least
  one active directed constituent minute.

The first later five-minute interaction then uses the same visual-response OR
causal-absorption entry, natural stop/target and single-account arbitration as
the proven flow-OB family.  No score, ATR, session or outcome rule is added.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle
from easychart_re1_complete_policy import DecisionOrderBlock
from easychart_re1_flow_ob import (
    OBFormationFlowEvidence,
    FlowValidatedOrderBlockDecisionStructureBook,
)
from easychart_re1_flow_ob_responsibility import (
    EasyChartRE1ResponsibleFlowOBBundle,
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_re1_reversal_flow_ob import EasyChartRE1ReversalFlowOBBundle
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


DOUBLE_ENGULFING_OB_RULE = (
    "SOURCE_EXPLICIT:"
    "CANDLE_B_ENGULFS_A_AND_CANDLE_C_ENGULFS_B_SO_B_BODY_IS_A_STRONG_DOUBLE_ENGULFING_ORDER_BLOCK"
)
DOUBLE_ENGULFING_LOCATION_RULE = (
    "SOURCE_EXPLICIT:"
    "DOUBLE_ENGULFING_ORDER_BLOCK_IS_MEANINGFUL_WHEN_FORMED_AT_PREEXISTING_STRUCTURE_OR_LIQUIDITY"
)
for _rule in (DOUBLE_ENGULFING_OB_RULE, DOUBLE_ENGULFING_LOCATION_RULE):
    if _rule not in _contracts.SOURCE_RULES:
        _contracts.SOURCE_RULES += (_rule,)


class DoubleEngulfingFlowOBBook(FlowValidatedOrderBlockDecisionStructureBook):
    """Detect and expose only contextual, flow-valid double engulfing zones."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.location_evidence: dict[str, tuple[str, ...]] = {}
        self._double_counts: dict[str, int] = {}

    def _einc(self, key: str) -> None:
        self._double_counts[key] = self._double_counts.get(key, 0) + 1

    @staticmethod
    def _body(bar: Candle) -> tuple[float, float, float]:
        lower = min(bar.open, bar.close)
        upper = max(bar.open, bar.close)
        return lower, upper, upper - lower

    @staticmethod
    def _bullish(bar: Candle) -> bool:
        return bar.close > bar.open

    def _engulfs(self, outer: Candle, inner: Candle) -> bool:
        ol, ou, ob = self._body(outer)
        il, iu, ib = self._body(inner)
        return ob > 0.0 and ib > 0.0 and ol <= il and ou >= iu

    def _location_ids(
        self,
        side: ZoneSide,
        formation: tuple[Candle, Candle, Candle],
    ) -> tuple[str, ...]:
        first = formation[0]
        boundaries = NearestAnyPivotStructureBook.boundaries_at(
            self,
            first.ts_close_ns,
        )
        output = {
            zone.source_structure_id
            for zone in boundaries
            if zone.side is side
            and zone.observed_time_ns < first.ts_close_ns
            and any(
                bar.low <= zone.upper and bar.high >= zone.lower
                for bar in formation
            )
        }
        return tuple(sorted(output))

    def _detect_double(self) -> PriceZone | None:
        if len(self.bars) < 3:
            return None
        a, b, c = self.bars[-3:]
        if self._bullish(a) == self._bullish(b) or self._bullish(b) == self._bullish(c):
            return None
        if not self._engulfs(b, a) or not self._engulfs(c, b):
            return None
        side = ZoneSide.SUPPORT if self._bullish(c) else ZoneSide.RESISTANCE
        bl, bu, bb = self._body(b)
        _, _, cb = self._body(c)
        if bb <= 0.0 or cb <= 0.0:
            return None
        invalidation = (
            min(a.low, b.low, c.low) - self.tick_size
            if side is ZoneSide.SUPPORT
            else max(a.high, b.high, c.high) + self.tick_size
        )
        return PriceZone(
            zone_id=(
                f"{self.symbol}:{self.timeframe_minutes}m:DOUBLE_ENGULFING:"
                f"{side.value}:{len(self.bars)-3}-{len(self.bars)-2}-{len(self.bars)-1}"
            ),
            kind=ZoneKind.ORDER_BLOCK,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            lower=bl,
            upper=bu,
            invalidation=invalidation,
            impulse_extreme=c.high if side is ZoneSide.SUPPORT else c.low,
            formed_index=len(self.bars) - 1,
            formed_time_ns=b.ts_close_ns,
            observed_time_ns=c.ts_close_ns,
            formation_indices=(len(self.bars)-3, len(self.bars)-2, len(self.bars)-1),
            strength_ratio=cb / bb,
            source_body_lower=bl,
            source_body_upper=bu,
        )

    def _register_double(self, zone: PriceZone) -> None:
        if zone.zone_id in self._source_ids:
            return
        formation = tuple(self.bars[i] for i in zone.formation_indices)
        locations = self._location_ids(zone.side, formation)  # type: ignore[arg-type]
        if not locations:
            self._source_ids.add(zone.zone_id)
            self._einc("double_engulfing_not_at_preexisting_structure")
            return
        evidence = self._formation_flow(zone)
        if evidence is None:
            self._source_ids.add(zone.zone_id)
            self._einc("contextual_double_engulfing_rejected_without_flow")
            return
        level = DecisionOrderBlock(
            level_id=f"DOUBLE_OB:{zone.zone_id}",
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
        self.flow_evidence[level.level_id] = evidence
        self.location_evidence[level.level_id] = locations
        self._dinc("double_engulfing_decision_ob_created")
        self._einc("contextual_flow_double_engulfing_validated")

    def on_bar(self, bar: Candle):  # type: ignore[no-untyped-def]
        result = NearestAnyPivotStructureBook.on_bar(self, bar)
        zone = self._detect_double()
        if zone is not None:
            self._einc("double_engulfing_detected")
            self._register_double(zone)
        return result

    @property
    def double_engulfing_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._double_counts.items())),
            "validated_levels": len(self.location_evidence),
            "formation_flow": self.flow_validation_diagnostics,
            "rules": (DOUBLE_ENGULFING_OB_RULE, DOUBLE_ENGULFING_LOCATION_RULE),
        }


class DoubleEngulfingDecisionAreaEngine(ResponsibleFlowValidatedDecisionAreaEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = DoubleEngulfingFlowOBBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )

    @property
    def double_engulfing_diagnostics(self) -> dict[str, Any]:
        return self.structure.double_engulfing_diagnostics


class EasyChartRE1DoubleEngulfingBundle(EasyChartRE1ReversalFlowOBBundle):
    """Reversal/flow-OB system plus an independent double-engulfing family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.double_flow_ob = DoubleEngulfingDecisionAreaEngine(
            symbol, tick_size, scale_name="DOUBLE_FLOW_OB", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["double_flow_ob"] = 0
        self._double_bundle_counts: dict[str, int] = {}
        self._double_bundle_trace: list[dict[str, Any]] = []

    def _binc(self, key: str) -> None:
        self._double_bundle_counts[key] = self._double_bundle_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.double_flow_ob.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.double_flow_ob.plans

    def _route_double(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(raw, key=lambda item: (item.interaction_time_ns, item.observed_time_ns, item.plan_id)):
            if plan.scenario_path not in {ScenarioPath.BOUNCE.value, ScenarioPath.REJECTION.value}:
                self._binc("double_engulfing_non_reversal_path_suppressed")
                continue
            if self._duplicate_episode(plan):
                self._binc("double_engulfing_overlapped_existing_episode")
                continue
            if not self._route_plan(plan):
                self._binc("double_engulfing_rejected_by_macro_context")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._binc("double_engulfing_plan_allowed")
            self._double_bundle_trace.append({
                "scenario_kind": "double_engulfing_plan_allowed",
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "side": plan.side.name,
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "gross_rr": plan.gross_rr,
                "rule_provenance": (DOUBLE_ENGULFING_OB_RULE, DOUBLE_ENGULFING_LOCATION_RULE),
            })
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        double: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw = self.double_flow_ob.on_bar(timeframe_minutes, bar)
            self._sync_audit("double_flow_ob", self.double_flow_ob)
            double = self._route_double(raw)
        routed = super().on_bar(timeframe_minutes, bar)
        return sorted(double + routed, key=lambda plan: (plan.interaction_time_ns, -plan.higher_timeframe_minutes, plan.symbol, plan.plan_id))

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self.double_flow_ob.drain_trace() + self._double_bundle_trace
        self._double_bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.double_flow_ob.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["double_engulfing_policy"] = {
            "counts": dict(sorted(self._double_bundle_counts.items())),
            "engine": self.double_flow_ob.double_engulfing_diagnostics,
            "rules": (DOUBLE_ENGULFING_OB_RULE, DOUBLE_ENGULFING_LOCATION_RULE),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DoubleEngulfingBundle
