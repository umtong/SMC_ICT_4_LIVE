"""Ordered-channel-phase causal-flow core for EasyChart RE1.

The focused flow pass showed that most losing current-absorption trades came
from a channel main edge exposed before the source's alternating four-point
sequence was complete.  This module unifies the two ideas rather than adding a
new filter:

* the existing channel-phase book decides whether a projected edge exists yet;
* current sweep/reclaim absorption or accepted-break/retest flow decides why
  to enter there;
* repeated absorption remains diagnostic; horizontal repeated-defense remains
  visual-only; ordinary OB/FVG and exact-retest entries remain an OR branch.

No fitted threshold, score, clock filter, ATR rule, risk change or post-entry
management is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_flow_focused import FocusedAuctionFlowMixin
from easychart_re1_natural_geometry import (
    EasyChartRE1NaturalGeometryBundle,
    NaturalHorizontalEngine,
    NaturalMajorSwingEngine,
    NaturalMicroEngine,
)
from easychart_re1_phase import ChannelPhaseStructureBook


PHASE_FLOW_RESPONSIBILITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ORDERED_FOUR_POINT_CHANNEL_PHASE_DEFINES_THE_BOUNDARY_AND_CAUSAL_FLOW_DEFINES_THE_ENTRY_EVENT"
)
if PHASE_FLOW_RESPONSIBILITY_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (PHASE_FLOW_RESPONSIBILITY_RULE,)


class PhaseFocusedFlowMicroEngine(FocusedAuctionFlowMixin, NaturalMicroEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = ChannelPhaseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class PhaseFocusedFlowMajorSwingEngine(
    FocusedAuctionFlowMixin,
    NaturalMajorSwingEngine,
):
    pass


class EasyChartRE1PhaseFlowBundle(EasyChartRE1NaturalGeometryBundle):
    """Small natural core with ordered channel geometry and focused flow."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = PhaseFocusedFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = NaturalHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = PhaseFocusedFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "horizontal", "major_swing"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["phase_focused_causal_flow"] = {
            "micro": self.micro.focused_flow_diagnostics,
            "channel_phase": self.micro.structure.phase_diagnostics,
            "horizontal": "VISUAL_ONLY",
            "major_swing": self.major_swing.focused_flow_diagnostics,
            "rule_provenance": PHASE_FLOW_RESPONSIBILITY_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1PhaseFlowBundle
