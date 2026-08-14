"""Flow-validated fifteen-minute order-block decision areas for EasyChart RE1.

A large participant cannot create a meaningful engulfing order block without
actually crossing liquidity and moving price.  The visual two-candle pattern is
therefore not enough by itself, but volume is also not a universal entry gate.
This module assigns aggressor flow two precise responsibilities:

1. validate the *birth* of a high-quality 15-minute engulfing OB using the
   completed one-minute trades inside its displacement candle;
2. at the first later interaction, allow either the ordinary event-local
   OB/FVG response or a current sweep/reclaim absorption event to trigger the
   immutable plan.

The formation is accepted only when cumulative taker imbalance and net price
progress agree with the OB direction and at least one constituent minute shows
above-baseline directed activity with material progress.  No fitted percentile,
score, ATR rule, session filter, trade limit, partial exit or stop movement is
introduced.  The existing natural 5m/15m invalidation and first-obstacle target
remain responsible for geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_complete_policy import (
    OrderBlockDecisionStructureBook,
    SourceFootprintLocatedMixin,
)
from easychart_re1_confirmed import ConfirmedRepeatedDefenseScenarioEngine
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_re1_flow_focused import FocusedAuctionFlowMixin
from easychart_re1_flow_phase import EasyChartRE1PhaseFlowBundle
from easychart_re1_natural_geometry import EpisodeLocalFVGMixin, NaturalGeometryMixin
from easychart_zones import PriceZone, ZoneKind, ZoneSide


FLOW_VALIDATED_OB_FORMATION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "A_HIGH_QUALITY_FIFTEEN_MINUTE_ENGULFING_OB_IS_A_DECISION_AREA_ONLY_WHEN_ITS_IMPULSE_CANDLE_HAS_ALIGNED_CUMULATIVE_TAKER_FLOW_AND_PRICE_PROGRESS"
)
FLOW_OB_FIRST_TOUCH_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_FIRST_LATER_TOUCH_OF_A_FLOW_VALIDATED_DECISION_OB_USES_EITHER_EVENT_LOCAL_VISUAL_RESPONSE_OR_CURRENT_SWEEP_RECLAIM_ABSORPTION"
)
for _rule in (FLOW_VALIDATED_OB_FORMATION_RULE, FLOW_OB_FIRST_TOUCH_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class OBFormationFlowEvidence:
    source_zone_id: str
    start_time_ns: int
    end_time_ns: int
    bars: int
    active_aligned_bars: int
    cumulative_signed_taker_quote: float
    net_price_progress: float
    strongest_activity_ratio: float
    strongest_delta_ratio: float
    strongest_body_ratio: float
    strength: float


class FlowValidatedOrderBlockDecisionStructureBook(OrderBlockDecisionStructureBook):
    """Expose only engulfing OBs whose displacement is real traded initiative."""

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        tick_size: float,
        flow_analyzer: CausalFlowAnalyzer,
    ) -> None:
        super().__init__(symbol, timeframe_minutes, tick_size)
        self.flow_analyzer = flow_analyzer
        self.flow_evidence: dict[str, OBFormationFlowEvidence] = {}
        self._flow_validation_counts: dict[str, int] = {}

    def _vinc(self, key: str) -> None:
        self._flow_validation_counts[key] = self._flow_validation_counts.get(key, 0) + 1

    @staticmethod
    def _aligned(side: ZoneSide, value: float) -> bool:
        return value > 0.0 if side is ZoneSide.SUPPORT else value < 0.0

    @staticmethod
    def _progress(side: ZoneSide, start: float, end: float) -> float:
        return end - start if side is ZoneSide.SUPPORT else start - end

    def _formation_flow(self, zone: PriceZone) -> OBFormationFlowEvidence | None:
        # ``formed_time_ns`` is the source candle close.  The causal
        # displacement candle is therefore exactly (formed, observed].
        observations = [
            item
            for item in self.flow_analyzer.history
            if zone.formed_time_ns < item.ts_close_ns <= zone.observed_time_ns
        ]
        if not observations:
            self._vinc("formation_missing_one_minute_flow")
            return None

        cumulative_delta = sum(item.signed_taker_quote for item in observations)
        net_progress = self._progress(
            zone.side,
            observations[0].open,
            observations[-1].close,
        )
        aligned = [
            item
            for item in observations
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(zone.side, item.signed_taker_quote)
            and (item.body > 0.0 if zone.side is ZoneSide.SUPPORT else item.body < 0.0)
        ]
        if not self._aligned(zone.side, cumulative_delta):
            self._vinc("formation_cumulative_taker_flow_not_aligned")
            return None
        if net_progress <= 0.0:
            self._vinc("formation_net_price_progress_not_aligned")
            return None
        if not aligned:
            self._vinc("formation_no_active_directed_progress_minute")
            return None

        strongest = max(
            aligned,
            key=lambda item: (
                item.activity_ratio * item.delta_ratio * item.body_ratio,
                item.ts_close_ns,
            ),
        )
        return OBFormationFlowEvidence(
            source_zone_id=zone.zone_id,
            start_time_ns=zone.formed_time_ns,
            end_time_ns=zone.observed_time_ns,
            bars=len(observations),
            active_aligned_bars=len(aligned),
            cumulative_signed_taker_quote=cumulative_delta,
            net_price_progress=net_progress,
            strongest_activity_ratio=strongest.activity_ratio,
            strongest_delta_ratio=strongest.delta_ratio,
            strongest_body_ratio=strongest.body_ratio,
            strength=(
                strongest.activity_ratio
                * strongest.delta_ratio
                * strongest.body_ratio
            ),
        )

    def _register(self, zone: PriceZone) -> None:
        if (
            zone.kind is not ZoneKind.ORDER_BLOCK
            or not zone.high_quality_by_size
            or zone.zone_id in self._source_ids
        ):
            return
        evidence = self._formation_flow(zone)
        if evidence is None:
            self._source_ids.add(zone.zone_id)
            self._vinc("formation_ob_rejected_without_flow")
            return
        super()._register(zone)
        level_id = f"DECISION_OB:{zone.zone_id}"
        self.flow_evidence[level_id] = evidence
        self._vinc("formation_ob_flow_validated")

    @property
    def flow_validation_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._flow_validation_counts.items())),
            "validated_levels": len(self.flow_evidence),
            "rules": (
                FLOW_VALIDATED_OB_FORMATION_RULE,
                FLOW_OB_FIRST_TOUCH_RULE,
            ),
        }


class FlowValidatedDecisionAreaEngine(
    FocusedAuctionFlowMixin,
    NaturalGeometryMixin,
    EpisodeLocalFVGMixin,
    SourceFootprintLocatedMixin,
    ConfirmedRepeatedDefenseScenarioEngine,
):
    """First-touch 15m OB auction with visual OR current-absorption entry."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = FlowValidatedOrderBlockDecisionStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )

    @property
    def formation_flow_diagnostics(self) -> dict[str, Any]:
        return self.structure.flow_validation_diagnostics


class EasyChartRE1PhaseFlowOBBundle(EasyChartRE1PhaseFlowBundle):
    """Ordered channel flow core plus one independent 15m OB opportunity family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.flow_decision_ob = FlowValidatedDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["flow_decision_ob"] = 0
        self._flow_ob_counts: dict[str, int] = {}
        self._flow_ob_trace: list[dict[str, Any]] = []

    def _oinc(self, key: str) -> None:
        self._flow_ob_counts[key] = self._flow_ob_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.flow_decision_ob.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.flow_decision_ob.plans

    def _route_flow_ob(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path not in {
                ScenarioPath.BOUNCE.value,
                ScenarioPath.REJECTION.value,
            }:
                self._oinc("non_retest_flow_ob_path_suppressed")
                continue
            if self._duplicate_episode(plan):
                self._oinc("flow_ob_overlapped_existing_episode")
                continue
            if not super()._route_plan(plan):
                self._oinc("flow_ob_rejected_by_macro_context")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._oinc("flow_ob_plan_allowed")
            self._flow_ob_trace.append(
                {
                    "scenario_kind": "flow_validated_decision_ob_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "scenario_path": plan.scenario_path,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        FLOW_VALIDATED_OB_FORMATION_RULE,
                        FLOW_OB_FIRST_TOUCH_RULE,
                    ),
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        routed = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return routed
        raw = self.flow_decision_ob.on_bar(timeframe_minutes, bar)
        self._sync_audit("flow_decision_ob", self.flow_decision_ob)
        return routed + self._route_flow_ob(raw)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.flow_decision_ob.drain_trace()
            + self._flow_ob_trace
        )
        self._flow_ob_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.flow_decision_ob.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["flow_validated_decision_ob"] = {
            "counts": dict(sorted(self._flow_ob_counts.items())),
            "formation": self.flow_decision_ob.formation_flow_diagnostics,
            "engine": self.flow_decision_ob.diagnostics,
            "geometry": self.flow_decision_ob.natural_geometry_diagnostics,
            "rules": (
                FLOW_VALIDATED_OB_FORMATION_RULE,
                FLOW_OB_FIRST_TOUCH_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1PhaseFlowOBBundle
