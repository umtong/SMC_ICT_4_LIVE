"""Scenario-mechanism-specific order-flow evidence for EasyChart RE1.

Aggressor flow is not interchangeable across scenarios:

* accepted breaks are continuation auctions and require initiative in the trade
  direction with material price progress;
* sweeps, rejections, bounces and terminal reversals are failed-auction events
  and require current or repeated absorption of aggression against the trade;
* ordinary OB/FVG and exact-retest entries remain unchanged and keep priority.

The previous flow candidate allowed aligned initiative to substitute inside a
rejection setup. That multiplied trade count but confused two opposite causal
stories: aggressive selling with downward progress is not evidence for a short
fakeout reversal; it is merely selling initiative. This module assigns each
observable flow pattern exactly one responsibility without adding a global
volume filter, score, fitted percentile, time-of-day rule or management change.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup
from easychart_re1_complete_policy import LocatedHorizontalFlipEngine
from easychart_re1_flow import (
    FlowEntryMixin,
    FlowSignal,
    FlowTriggerKind,
)
from easychart_re1_flow_routed import EasyChartRE1FlowRoutedBundle
from easychart_re1_human_policy import (
    HumanDecisionAreaEngine,
    HumanHorizontalEngine,
    HumanMajorSwingEngine,
    HumanMicroEngine,
)
from easychart_re1_wedge import TerminalWedgeScenarioEngine


FLOW_MECHANISM_RESPONSIBILITY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ACCEPTED_BREAKS_USE_ALIGNED_INITIATIVE_WHILE_REJECTION_BOUNCE_AND_WEDGE_REVERSALS_USE_OPPOSING_AGGRESSION_ABSORPTION"
)
if FLOW_MECHANISM_RESPONSIBILITY_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (FLOW_MECHANISM_RESPONSIBILITY_RULE,)


_INITIATIVE = {
    FlowTriggerKind.BUY_INITIATIVE,
    FlowTriggerKind.SELL_INITIATIVE,
}
_ABSORPTION = {
    FlowTriggerKind.SELL_ABSORPTION,
    FlowTriggerKind.BUY_ABSORPTION,
    FlowTriggerKind.REPEATED_SELL_ABSORPTION,
    FlowTriggerKind.REPEATED_BUY_ABSORPTION,
}
_REVERSAL_PATHS = {
    ScenarioPath.REJECTION,
    ScenarioPath.BOUNCE,
    ScenarioPath.ROTATION,
}


class MechanismFlowEntryMixin(FlowEntryMixin):
    """Keep only the flow mechanism which explains the scenario path."""

    def _flow_signal(
        self,
        setup: ScenarioSetup,
        bar: Any,
        observation: Any,
    ) -> FlowSignal | None:
        signal = super()._flow_signal(setup, bar, observation)
        if signal is None:
            return None

        if setup.path is ScenarioPath.ACCEPTANCE:
            if signal.kind in _INITIATIVE:
                self._finc("mechanism_acceptance_initiative_allowed")
                return signal
            self._finc("mechanism_acceptance_absorption_deferred")
            return None

        if setup.path in _REVERSAL_PATHS:
            if signal.kind in _ABSORPTION:
                self._finc("mechanism_reversal_absorption_allowed")
                return signal
            self._finc("mechanism_reversal_initiative_deferred")
            return None

        self._finc("mechanism_unknown_path_deferred")
        return None

    @property
    def mechanism_flow_diagnostics(self) -> dict[str, Any]:
        return {
            "acceptance": tuple(sorted(kind.value for kind in _INITIATIVE)),
            "reversal_paths": tuple(sorted(path.value for path in _REVERSAL_PATHS)),
            "reversal": tuple(sorted(kind.value for kind in _ABSORPTION)),
            "rule_provenance": FLOW_MECHANISM_RESPONSIBILITY_RULE,
        }


class MechanismFlowHumanMicroEngine(MechanismFlowEntryMixin, HumanMicroEngine):
    pass


class MechanismFlowHumanHorizontalEngine(MechanismFlowEntryMixin, HumanHorizontalEngine):
    pass


class MechanismFlowHumanMajorSwingEngine(MechanismFlowEntryMixin, HumanMajorSwingEngine):
    pass


class MechanismFlowHumanDecisionAreaEngine(
    MechanismFlowEntryMixin,
    HumanDecisionAreaEngine,
):
    pass


class MechanismFlowHorizontalFlipEngine(
    MechanismFlowEntryMixin,
    LocatedHorizontalFlipEngine,
):
    pass


class MechanismFlowTerminalWedgeScenarioEngine(
    MechanismFlowEntryMixin,
    TerminalWedgeScenarioEngine,
):
    pass


class EasyChartRE1MechanismFlowBundle(EasyChartRE1FlowRoutedBundle):
    """Routed flow system with initiative and absorption responsibilities split."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = MechanismFlowHumanMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = MechanismFlowHumanHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = MechanismFlowHumanMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.decision_area = MechanismFlowHumanDecisionAreaEngine(
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
        self.wedge = MechanismFlowTerminalWedgeScenarioEngine(
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
        output["mechanism_specific_flow_policy"] = {
            "micro": self.micro.mechanism_flow_diagnostics,
            "horizontal": self.horizontal.mechanism_flow_diagnostics,
            "major_swing": self.major_swing.mechanism_flow_diagnostics,
            "decision_area": self.decision_area.mechanism_flow_diagnostics,
            "horizontal_flip": self.horizontal_flip.mechanism_flow_diagnostics,
            "terminal_wedge": self.wedge.mechanism_flow_diagnostics,
            "rule_provenance": FLOW_MECHANISM_RESPONSIBILITY_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1MechanismFlowBundle
