"""Diagnostic abstention from channel-edge reversal execution.

The supplied channel material gives channels several distinct responsibilities:
ordered fourth-point reversal, fakeout/re-entry, midline phase and accepted
breakout.  Treating every fourth-point one-minute absorption as an immediately
executable fade collapsed those states into one label.  Across the expanded
RE1 diagnostics, channel-labelled reversals were the only large persistent drag,
while standalone wick trend-line reversals remained useful.

This ablation does not claim that channels have no alpha.  It asks the narrower,
high-value question: does the current account improve when channel-edge
reversals remain observable but cannot submit plans until a dedicated channel
phase-transition family is rebuilt?  Horizontal sweeps, standalone trend lines,
major swings, the original flow-validated 15-minute OB family, execution, costs,
stops and objectives are unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, SetupState, StructureFamily
from domain import Candle
from easychart_re1_reversal_flow_ob import (
    EasyChartRE1ReversalFlowOBBundle,
    ReversalOnlyResponsiblePhaseFlowMicroEngine,
)


CHANNEL_REVERSAL_ABSTENTION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CHANNEL_EDGE_REVERSAL_REMAINS_DIAGNOSTIC_UNTIL_A_DEDICATED_PHASE_TRANSFER_ENTRY_REPLACES_IMMEDIATE_EDGE_ABSORPTION"
)
if CHANNEL_REVERSAL_ABSTENTION_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (CHANNEL_REVERSAL_ABSTENTION_RULE,)


class ChannelAbstainingMicroEngine(ReversalOnlyResponsiblePhaseFlowMicroEngine):
    """Retire newly armed channel reversals without affecting other structures."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._channel_abstention_counts: dict[str, int] = {}

    def _cinc(self, key: str) -> None:
        self._channel_abstention_counts[key] = self._channel_abstention_counts.get(key, 0) + 1

    def _discover_interactions(self, bar: Candle, previous: Candle, index: int) -> None:
        before = len(self.setups)
        super()._discover_interactions(bar, previous, index)
        for setup in self.setups[before:]:
            if setup.path is not ScenarioPath.REJECTION:
                continue
            members = tuple(getattr(setup, "context_members", ())) or (setup.context,)
            if not any(member.family is StructureFamily.CHANNEL for member in members):
                continue
            self._active.pop(setup.setup_id, None)
            setup.state = SetupState.UNRESOLVED
            setup.terminal_reason = "channel_reversal_deferred_to_dedicated_phase_transfer"
            self._inc("channel_reversal_deferred_to_dedicated_phase_transfer")
            self._cinc("channel_reversal_abstained")
            self._trace(
                "channel_reversal_deferred_to_dedicated_phase_transfer",
                bar.ts_close_ns,
                setup,
                context_member_ids=[member.source_structure_id for member in members],
                context_member_families=[member.family.value for member in members],
                rule_provenance=CHANNEL_REVERSAL_ABSTENTION_RULE,
            )

    @property
    def channel_abstention_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._channel_abstention_counts.items())),
            "executable": False,
            "rule_provenance": CHANNEL_REVERSAL_ABSTENTION_RULE,
        }


class EasyChartRE1ChannelAbstentionBundle(EasyChartRE1ReversalFlowOBBundle):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ChannelAbstainingMicroEngine(
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
        output["channel_reversal_abstention"] = self.micro.channel_abstention_diagnostics
        return output


MultiScaleScenarioBundle = EasyChartRE1ChannelAbstentionBundle
