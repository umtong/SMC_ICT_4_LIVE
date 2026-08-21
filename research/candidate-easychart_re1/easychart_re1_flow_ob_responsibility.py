"""Single-entry evidence ownership with the original flow-validated 15m OB family.

The strict pre-existing-pivot sweep experiment was useful as an ablation, but it
removed nearly every 15-minute OB opportunity.  The supplied material defines
an important OB more broadly: it forms at meaningful structure or liquidity,
and its impulse must be real traded displacement.  The already profitable
flow-validated OB family encoded that birth evidence correctly.

This module therefore changes only the entry-ownership error discovered in the
flow traces:

* when the current one-minute bar creates a valid event-local OB/FVG, that visual
  footprint owns the future first-return decision;
* flow may replace a missing footprint, but cannot use the same completed bar to
  create a zone and skip its mitigation;
* accepted-break exact-retest flow remains unchanged;
* the independent 15-minute flow-validated OB family remains available without
  the narrower confirmed-pivot sweep prerequisite.

No threshold, score, time window, symbol rule or PnL-dependent choice is added.
"""
from __future__ import annotations

from typing import Any

from easychart_re1_flow_ob import (
    EasyChartRE1PhaseFlowOBBundle,
    FlowValidatedDecisionAreaEngine,
    FlowValidatedOrderBlockDecisionStructureBook,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
    ResponsiblePhaseFlowMicroEngine,
    VisualFootprintOwnsCurrentBarFlowMixin,
)


class ResponsibleFlowValidatedDecisionAreaEngine(
    VisualFootprintOwnsCurrentBarFlowMixin,
    FlowValidatedDecisionAreaEngine,
):
    """Original flow-valid 15m OB birth with unambiguous 1m entry ownership."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = FlowValidatedOrderBlockDecisionStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )


class EasyChartRE1ResponsibleFlowOBBundle(EasyChartRE1PhaseFlowOBBundle):
    """Ordered phase core, original flow-valid OBs and one evidence owner."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = ResponsiblePhaseFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = ResponsibleFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = ResponsibleFlowValidatedDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["single_entry_evidence_owner"] = {
            "micro": self.micro.entry_owner_diagnostics,
            "major_swing": self.major_swing.entry_owner_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.entry_owner_diagnostics,
            "formation": self.flow_decision_ob.formation_flow_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ResponsibleFlowOBBundle
