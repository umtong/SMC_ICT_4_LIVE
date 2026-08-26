"""Mechanism-flow-qualified immediate order-block entry for EasyChart RE1.

The causal-state candidate recovered trade frequency, but its completed-account
evidence showed a structural failure: the immediate high-quality one-minute OB
branch supplied most trades and most losses. Candle-size contrast identified a
visible footprint, but did not establish that a large auction was occurring at
that moment.

This module changes one responsibility only:

* a high-quality engulfing OB remains an immediate entry when its completed
  one-minute bar also carries the flow mechanism appropriate to the scenario;
* accepted breaks use aligned initiative, while rejection/bounce/rotation and
  terminal reversals use opposing-aggression absorption;
* without the matching flow event, the OB is not discarded--it falls back to
  the existing departure, first-retest and response path;
* the OB remains the price location while the flow mechanism becomes the plan's
  entry trigger kind, so the mechanism-aware router can route it correctly.

No fitted score, percentile, clock window, family exception, partial exit or
post-entry rule is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, V5TradePlan
from domain import Side
from easychart_re1_flow import FlowSignal
from easychart_re1_flow_mechanism import (
    EasyChartRE1MechanismFlowBundle,
    MechanismFlowHorizontalFlipEngine,
    MechanismFlowHumanDecisionAreaEngine,
    MechanismFlowHumanHorizontalEngine,
    MechanismFlowHumanMajorSwingEngine,
    MechanismFlowHumanMicroEngine,
    MechanismFlowTerminalWedgeScenarioEngine,
)
from easychart_zones import PriceZone, ZoneKind, ZoneSide


FLOW_QUALIFIED_IMMEDIATE_OB_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "HIGH_QUALITY_ENGULFING_OB_ENTERS_IMMEDIATELY_ONLY_WHEN_ITS_COMPLETED_BAR_ALSO_SHOWS_THE_FLOW_MECHANISM_APPROPRIATE_TO_THE_SCENARIO"
)
if FLOW_QUALIFIED_IMMEDIATE_OB_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (FLOW_QUALIFIED_IMMEDIATE_OB_RULE,)


class FlowQualifiedImmediateOrderBlockMixin:
    """Authorize immediate OBs with mechanism flow; otherwise retain retest."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._qualified_ob_signals: dict[str, FlowSignal] = {}
        self._qualified_ob_counts: dict[str, int] = {}

    def _qinc(self, key: str) -> None:
        self._qualified_ob_counts[key] = self._qualified_ob_counts.get(key, 0) + 1

    @staticmethod
    def _wanted_side(setup: ScenarioSetup) -> ZoneSide:
        return ZoneSide.SUPPORT if setup.side is Side.LONG else ZoneSide.RESISTANCE

    def _direct_candidates(
        self,
        setup: ScenarioSetup,
        created: list[PriceZone],
    ) -> list[PriceZone]:
        bar = self._current_trigger_bar
        if setup.confirmation_time_ns is None or bar is None:
            return []
        wanted = self._wanted_side(setup)
        candidates = [
            zone
            for zone in created
            if zone.kind is ZoneKind.ORDER_BLOCK
            and zone.side is wanted
            and zone.high_quality_by_size
            and zone.observed_time_ns > setup.confirmation_time_ns
            and self._formation_touches_context(zone, setup)
        ]
        if not candidates:
            return []

        signal = self._flow_signal(setup, bar, self._flow_current)
        if signal is None:
            self._qinc("strong_ob_deferred_to_retest_without_matching_flow")
            self._trace(
                "strong_ob_deferred_to_retest_without_matching_flow",
                bar.ts_close_ns,
                setup,
                candidate_zone_ids=[zone.zone_id for zone in candidates],
                rule_provenance=FLOW_QUALIFIED_IMMEDIATE_OB_RULE,
            )
            return []

        self._qualified_ob_signals[setup.setup_id] = signal
        self._qinc("strong_ob_immediate_authorized_by_matching_flow")
        self._trace(
            "strong_ob_immediate_authorized_by_matching_flow",
            bar.ts_close_ns,
            setup,
            candidate_zone_ids=[zone.zone_id for zone in candidates],
            rule_provenance=FLOW_QUALIFIED_IMMEDIATE_OB_RULE,
            **self._signal_trace(signal),
        )
        return candidates

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
    ) -> V5TradePlan | None:
        signal = self._qualified_ob_signals.pop(setup.setup_id, None)
        if signal is None:
            return super()._make_plan(
                setup,
                bar,
                entry=entry,
                stop=stop,
                trigger_zone=trigger_zone,
                trigger_kind=trigger_kind,
                trigger_strength=trigger_strength,
            )

        plan = super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=stop,
            trigger_zone=trigger_zone,
            trigger_kind=signal.kind,
            trigger_strength=signal.strength,
        )
        if plan is None:
            self._qinc("flow_qualified_strong_ob_geometry_rejected")
            return None
        self._qinc("flow_qualified_strong_ob_plan_created")
        self._trace(
            "flow_qualified_strong_ob_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            order_block_zone_id=getattr(trigger_zone, "zone_id", None),
            order_block_strength=trigger_strength,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            gross_rr=plan.gross_rr,
            rule_provenance=FLOW_QUALIFIED_IMMEDIATE_OB_RULE,
            **self._signal_trace(signal),
        )
        return plan

    @property
    def flow_qualified_ob_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._qualified_ob_counts.items())),
            "rule_provenance": FLOW_QUALIFIED_IMMEDIATE_OB_RULE,
        }


class QualifiedFlowMicroEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    MechanismFlowHumanMicroEngine,
):
    pass


class QualifiedFlowHorizontalEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    MechanismFlowHumanHorizontalEngine,
):
    pass


class QualifiedFlowMajorSwingEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    MechanismFlowHumanMajorSwingEngine,
):
    pass


class QualifiedFlowDecisionAreaEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    MechanismFlowHumanDecisionAreaEngine,
):
    pass


class QualifiedFlowTerminalWedgeEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    MechanismFlowTerminalWedgeScenarioEngine,
):
    pass


class EasyChartRE1FlowQualifiedOBBundle(EasyChartRE1MechanismFlowBundle):
    """Mechanism-flow system with qualified immediate OB entry."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = QualifiedFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = QualifiedFlowHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = QualifiedFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.decision_area = QualifiedFlowDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal_flip = MechanismFlowHorizontalFlipEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.wedge = QualifiedFlowTerminalWedgeEngine(
            symbol,
            tick_size,
            scale_name="TERMINAL_WEDGE",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in (
            "micro",
            "horizontal",
            "major_swing",
            "decision_area",
            "horizontal_flip",
            "wedge",
        ):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["flow_qualified_immediate_ob_policy"] = {
            "micro": self.micro.flow_qualified_ob_diagnostics,
            "horizontal": self.horizontal.flow_qualified_ob_diagnostics,
            "major_swing": self.major_swing.flow_qualified_ob_diagnostics,
            "decision_area": self.decision_area.flow_qualified_ob_diagnostics,
            "terminal_wedge": self.wedge.flow_qualified_ob_diagnostics,
            "rule_provenance": FLOW_QUALIFIED_IMMEDIATE_OB_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FlowQualifiedOBBundle
