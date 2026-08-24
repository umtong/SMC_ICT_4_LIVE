"""Causal channel-reversal policy for the canonical RE1 bot.

One channel-liquidity episode owns the full decision sequence:

1. the fourth channel point sweeps and closes back inside;
2. the next completed five-minute bar keeps the boundary reclaimed;
3. an event-local OB/FVG owns its first return when one formed;
4. otherwise current absorption is only evidence, not an entry;
5. flow-only evidence must complete a five-minute control transfer and then
   hold the first later return to the reclaimed dynamic boundary.

This composes the strongest coherent descendants of the original phase-flow
lineage without adding a score, fitted threshold, clock exit, risk overlay or
outcome-derived symbol rule.  Stops, first causal objectives, one-position
arbitration and the fixed three-percent account risk remain inherited.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_channel_rejection_hold import (
    CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE,
    EasyChartRE1ChannelRejectionHoldBundle,
    HeldChannelRejectionMicroEngine,
)
from easychart_re1_control_transfer import (
    DECISION_FRAME_CONTROL_TRANSFER_RULE,
    IMPACT_EFFICIENCY_TRANSFER_RULE,
)
from easychart_re1_control_transfer_retest_core import (
    CONTROL_TRANSFER_FIRST_RETEST_RULE,
    CONTROL_TRANSFER_SWEEP_INVALIDATION_RULE,
    DecisionFrameFirstRetestMixin,
)


CAUSAL_CHANNEL_REVERSAL_SEQUENCE_RULE = (
    "RESEARCH_SYNTHESIS:"
    "CHANNEL_REJECTION_REQUIRES_NEXT_DECISION_HOLD_AND_FLOW_ONLY_ENTRY_REQUIRES_COMPLETED_CONTROL_TRANSFER_THEN_FIRST_HELD_BOUNDARY_RETURN"
)
if CAUSAL_CHANNEL_REVERSAL_SEQUENCE_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (CAUSAL_CHANNEL_REVERSAL_SEQUENCE_RULE,)


class CausalChannelReversalMicroEngine(
    DecisionFrameFirstRetestMixin,
    HeldChannelRejectionMicroEngine,
):
    """Preserve visual first-return ownership and delay flow-only substitution."""


class EasyChartRE1CausalChannelBundle(EasyChartRE1ChannelRejectionHoldBundle):
    """One complete channel-reversal state machine in the existing RE1 account."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = CausalChannelReversalMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["causal_channel_reversal"] = {
            "sequence": (
                "SWEEP_RECLAIM",
                "NEXT_COMPLETED_5M_HOLD",
                "VISUAL_FIRST_RETURN_OR_FLOW_ONLY_CONTROL_TRANSFER",
                "FIRST_HELD_DYNAMIC_BOUNDARY_RETURN",
            ),
            "control_transfer": self.micro.decision_frame_control_transfer_diagnostics,
            "first_retest": self.micro.control_transfer_retest_diagnostics,
            "rules": (
                CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE,
                DECISION_FRAME_CONTROL_TRANSFER_RULE,
                IMPACT_EFFICIENCY_TRANSFER_RULE,
                CONTROL_TRANSFER_FIRST_RETEST_RULE,
                CONTROL_TRANSFER_SWEEP_INVALIDATION_RULE,
                CAUSAL_CHANNEL_REVERSAL_SEQUENCE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1CausalChannelBundle
