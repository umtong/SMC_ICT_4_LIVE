"""Significant-objective reversals with completed-frame flow control transfer.

A current one-minute absorption bar is evidence that aggressive orders failed to
move price, but it is not yet a complete reversal.  Visual OB/FVG first returns
keep their existing ownership.  Only the flow-only substitute is delayed until
a completed five-minute frame reclaims the traded boundary and the interaction
balance without a new adverse extreme, using the existing impact-efficiency
control-transfer mechanism.

This changes the causal decision rather than adding a score or a fitted filter:

* visual first-return rejection remains executable as before;
* accepted breaks retain their completed first-response entry;
* flow-only rejection becomes sweep -> completed control transfer -> entry;
* the nearest live 5m/15m or significant span-6 1m objective remains fixed
  before submission;
* one full position, natural invalidation, 3% NAV risk and NautilusTrader
  execution are unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState
from easychart_re1_control_transfer import (
    DECISION_FRAME_CONTROL_TRANSFER_RULE,
    IMPACT_EFFICIENCY_TRANSFER_RULE,
    DecisionFrameControlTransferMixin,
)
from easychart_re1_significant_response import (
    EasyChartRE1SignificantResponseBundle,
    SignificantResponseDecisionOBEngine,
    SignificantResponseMajorSwingEngine,
    SignificantResponseMicroEngine,
)


CONTROLLED_SIGNIFICANT_REVERSAL_RULE = (
    "RESEARCH_SYNTHESIS:FLOW_ONLY_SIGNIFICANT_OBJECTIVE_REVERSAL_REQUIRES_"
    "COMPLETED_FIVE_MINUTE_CONTROL_TRANSFER_WHILE_VISUAL_FIRST_RETURN_REMAINS_DIRECT"
)
if CONTROLLED_SIGNIFICANT_REVERSAL_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (CONTROLLED_SIGNIFICANT_REVERSAL_RULE,)


class ManagedControlTransferMixin(DecisionFrameControlTransferMixin):
    """Remove parked flow state whenever the owning structure episode ends."""

    def _finish(
        self,
        setup: Any,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        self._pending_control_transfers.pop(setup.setup_id, None)
        super()._finish(setup, state, time_ns, reason, **values)


class ControlledSignificantMicroEngine(
    ManagedControlTransferMixin,
    SignificantResponseMicroEngine,
):
    pass


class ControlledSignificantMajorSwingEngine(
    ManagedControlTransferMixin,
    SignificantResponseMajorSwingEngine,
):
    pass


class ControlledSignificantDecisionOBEngine(
    ManagedControlTransferMixin,
    SignificantResponseDecisionOBEngine,
):
    pass


class EasyChartRE1ControlledSignificantResponseBundle(
    EasyChartRE1SignificantResponseBundle
):
    """Visual rejection plus delayed flow-only transfer under one objective policy."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = ControlledSignificantMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = ControlledSignificantMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = ControlledSignificantDecisionOBEngine(
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

    @staticmethod
    def _control_diagnostics(engine: Any) -> dict[str, Any]:
        return engine.decision_frame_control_transfer_diagnostics

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["controlled_significant_reversal"] = {
            "micro": self._control_diagnostics(self.micro),
            "major_swing": self._control_diagnostics(self.major_swing),
            "flow_decision_ob": self._control_diagnostics(self.flow_decision_ob),
            "visual_rejection_entry": "UNCHANGED_FIRST_RETURN_OR_RESPONSE",
            "flow_only_rejection_entry": "COMPLETED_5M_CONTROL_TRANSFER",
            "rules": (
                DECISION_FRAME_CONTROL_TRANSFER_RULE,
                IMPACT_EFFICIENCY_TRANSFER_RULE,
                CONTROLLED_SIGNIFICANT_REVERSAL_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ControlledSignificantResponseBundle
