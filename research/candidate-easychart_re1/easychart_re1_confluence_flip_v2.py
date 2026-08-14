"""Clean confluence-flip ablation with the prior reversal core unchanged."""
from __future__ import annotations

from contracts_v5 import ScenarioPath, SetupState
from domain import Candle
from easychart_re1_confluence_flip import (
    CONFLUENCE_FLIP_RULE,
    ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE,
    ConfluenceAcceptanceEngine,
    EasyChartRE1ConfluenceFlipBundle as _ConfluenceBundle,
)
from easychart_re1_flow_ob_sweep_responsibility import ResponsiblePhaseFlowMicroEngine


class ReversalOnlyPhaseFlowMicroEngine(ResponsiblePhaseFlowMicroEngine):
    """Preserve the tested phase-flow reversal engine and retire only acceptance."""

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


class EasyChartRE1ConfluenceFlipBundle(_ConfluenceBundle):
    """Sweep-valid OB account plus unchanged reversals and dedicated confluence flips."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ReversalOnlyPhaseFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0


MultiScaleScenarioBundle = EasyChartRE1ConfluenceFlipBundle
