"""Sequence-flow-qualified immediate OBs for the complete EasyChart RE1 system.

This candidate combines the two strongest structural diagnoses obtained so far:

* immediate one-minute engulfing OBs were frequent but usually unprofitable when
  candle-size contrast alone labeled them institutional;
* one-bar initiative was also too permissive, whereas a boundary absorption ->
  reclaim/response sequence matches the actual liquidity mechanism.

The same sequence signal now serves both roles. A strong OB can enter at its
completed close only when it is the reclaiming absorption bar or the first
aligned initiative after boundary absorption. Otherwise it remains available to
its ordinary departure/first-retest/response path. All non-OB visual entries and
all independent sequence-flow entries remain unchanged.
"""
from __future__ import annotations

from typing import Any

from easychart_re1_flow_qualified_ob import FlowQualifiedImmediateOrderBlockMixin
from easychart_re1_flow_sequence import (
    EasyChartRE1SequenceFlowBundle,
    SequenceFlowDecisionAreaEngine,
    SequenceFlowHorizontalEngine,
    SequenceFlowHorizontalFlipEngine,
    SequenceFlowMajorSwingEngine,
    SequenceFlowMicroEngine,
    SequenceFlowTerminalWedgeEngine,
)


class SequenceQualifiedMicroEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    SequenceFlowMicroEngine,
):
    pass


class SequenceQualifiedHorizontalEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    SequenceFlowHorizontalEngine,
):
    pass


class SequenceQualifiedMajorSwingEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    SequenceFlowMajorSwingEngine,
):
    pass


class SequenceQualifiedDecisionAreaEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    SequenceFlowDecisionAreaEngine,
):
    pass


class SequenceQualifiedTerminalWedgeEngine(
    FlowQualifiedImmediateOrderBlockMixin,
    SequenceFlowTerminalWedgeEngine,
):
    pass


class EasyChartRE1SequenceQualifiedOBBundle(EasyChartRE1SequenceFlowBundle):
    """Sequence flow plus sequence-qualified immediate OB entry."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = SequenceQualifiedMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = SequenceQualifiedHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = SequenceQualifiedMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.decision_area = SequenceQualifiedDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal_flip = SequenceFlowHorizontalFlipEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.wedge = SequenceQualifiedTerminalWedgeEngine(
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
        output["sequence_qualified_immediate_ob_policy"] = {
            "micro": self.micro.flow_qualified_ob_diagnostics,
            "horizontal": self.horizontal.flow_qualified_ob_diagnostics,
            "major_swing": self.major_swing.flow_qualified_ob_diagnostics,
            "decision_area": self.decision_area.flow_qualified_ob_diagnostics,
            "terminal_wedge": self.wedge.flow_qualified_ob_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1SequenceQualifiedOBBundle
