"""Small EasyChart RE1 core with mechanism-specific causal aggressor flow.

This candidate intentionally returns to the three responsibilities that were
already present before decision-area, wedge and duplicated S/R-flip families
expanded the opportunity graph:

* diagonal/channel auction at a meaningful boundary;
* repeated-defense horizontal liquidity auction;
* one confirmed major swing liquidity auction.

Natural five-/fifteen-minute stop and first-obstacle target geometry are kept.
Volume is not an extra universal condition: ordinary source-supported OB/FVG
or exact-retest entries remain executable, while coherent initiative or
absorption can substitute for a missing one-minute visual footprint according
to the scenario mechanism.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_flow_mechanism import MechanismFlowEntryMixin
from easychart_re1_natural_geometry import (
    EasyChartRE1NaturalGeometryBundle,
    NaturalHorizontalEngine,
    NaturalMajorSwingEngine,
    NaturalMicroEngine,
)


FLOW_CORE_RESPONSIBILITY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CAUSAL_FLOW_RECOVERS_OPPORTUNITIES_INSIDE_THE_THREE_NATURAL_STRUCTURE_FAMILIES_WITHOUT_DECISION_AREA_WEDGE_OR_DUPLICATED_FLIP_FAMILIES"
)
if FLOW_CORE_RESPONSIBILITY_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (FLOW_CORE_RESPONSIBILITY_RULE,)


class FlowCoreMicroEngine(MechanismFlowEntryMixin, NaturalMicroEngine):
    pass


class FlowCoreHorizontalEngine(MechanismFlowEntryMixin, NaturalHorizontalEngine):
    pass


class FlowCoreMajorSwingEngine(MechanismFlowEntryMixin, NaturalMajorSwingEngine):
    pass


class EasyChartRE1FlowCoreBundle(EasyChartRE1NaturalGeometryBundle):
    """Three-family fixed-plan system with order flow as an OR entry branch."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = FlowCoreMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = FlowCoreHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = FlowCoreMajorSwingEngine(
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
        output["small_causal_flow_core"] = {
            "families": ("MICRO", "HORIZONTAL", "LIQUIDITY"),
            "micro": self.micro.mechanism_flow_diagnostics,
            "horizontal": self.horizontal.mechanism_flow_diagnostics,
            "major_swing": self.major_swing.mechanism_flow_diagnostics,
            "rule_provenance": FLOW_CORE_RESPONSIBILITY_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FlowCoreBundle
