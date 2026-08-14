"""Reversal-only phase-flow core plus the original flow-validated 15m OB family.

Generic isolated diagonal acceptance is assigned to a separate complete S/R-flip
scenario. This module keeps rejection/rotation, the original flow-valid 15m OB
family, single-entry evidence ownership and the unchanged account stack.
"""
from __future__ import annotations

from contracts_v5 import ScenarioPath, SetupState
from domain import Candle
from easychart_re1_confluence_flip import ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE
from easychart_re1_flow_ob_responsibility import EasyChartRE1ResponsibleFlowOBBundle
from easychart_re1_flow_ob_sweep_responsibility import ResponsiblePhaseFlowMicroEngine


class ReversalOnlyResponsiblePhaseFlowMicroEngine(ResponsiblePhaseFlowMicroEngine):
    def _discover_interactions(self, bar: Candle, previous: Candle, index: int) -> None:
        before = len(self.setups)
        super()._discover_interactions(bar, previous, index)
        for setup in self.setups[before:]:
            if setup.path is not ScenarioPath.ACCEPTANCE:
                continue
            self._active.pop(setup.setup_id, None)
            setup.state = SetupState.UNRESOLVED
            setup.terminal_reason = "isolated_diagonal_acceptance_deferred_to_confluence"
            self._inc("isolated_diagonal_acceptance_deferred_to_confluence")
            self._trace(
                "isolated_diagonal_acceptance_deferred_to_confluence",
                bar.ts_close_ns,
                setup,
                rule_provenance=ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE,
            )


class EasyChartRE1ReversalFlowOBBundle(EasyChartRE1ResponsibleFlowOBBundle):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ReversalOnlyResponsiblePhaseFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0


MultiScaleScenarioBundle = EasyChartRE1ReversalFlowOBBundle
