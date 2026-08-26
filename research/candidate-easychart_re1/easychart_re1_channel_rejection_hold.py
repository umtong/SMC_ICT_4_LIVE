"""Next-decision-bar hold for channel fakeout/rejection entries.

A one-minute absorption immediately after the fourth-point channel sweep can be
the first pause of a continuing breakout.  The source distinguishes a genuine
channel fakeout by a completed close back inside and, for confirmation trading,
by the reclaimed boundary continuing to hold.  This module changes only that
responsibility:

* a full 5-minute channel sweep/reclaim no longer arms one-minute entry on the
  same decision close;
* the next completed 5-minute bar must still close inside the channel boundary;
* only then may the inherited visual-footprint or causal-flow entry paths act.

Non-channel rejections, partial reclaim episodes, accepted breaks, stops,
targets, costs and account routing are unchanged.  No clock or fitted threshold
is added; the next decision bar is the complete hold event.
"""
from __future__ import annotations

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, SetupState, StructureFamily
from domain import Candle
from easychart_re1_reversal_flow_ob import (
    EasyChartRE1ReversalFlowOBBundle,
    ReversalOnlyResponsiblePhaseFlowMicroEngine,
)


CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_CHANNEL_SWEEP_RECLAIM_ARMS_LOWER_FRAME_ENTRY_ONLY_AFTER_THE_NEXT_COMPLETED_DECISION_BAR_STILL_CLOSES_INSIDE_THE_RECLAIMED_BOUNDARY"
)
if CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE,)


class HeldChannelRejectionMicroEngine(ReversalOnlyResponsiblePhaseFlowMicroEngine):
    """Turn immediate full channel rejection into one next-bar hold state."""

    def _discover_interactions(self, bar: Candle, previous: Candle, index: int) -> None:
        before = len(self.setups)
        super()._discover_interactions(bar, previous, index)
        for setup in self.setups[before:]:
            if (
                setup.path is ScenarioPath.REJECTION
                and setup.state is SetupState.WAITING_DISPLACEMENT
                and any(
                    member.family is StructureFamily.CHANNEL
                    for member in setup.context_members
                )
            ):
                setup.state = SetupState.WAITING_RECLAIM
                setup.confirmation_time_ns = None
                self._inc("channel_rejection_waiting_next_decision_hold")
                self._trace(
                    "channel_rejection_waiting_next_decision_hold",
                    bar.ts_close_ns,
                    setup,
                    rule_provenance=CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE,
                )


class EasyChartRE1ChannelRejectionHoldBundle(EasyChartRE1ReversalFlowOBBundle):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = HeldChannelRejectionMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0


MultiScaleScenarioBundle = EasyChartRE1ChannelRejectionHoldBundle
