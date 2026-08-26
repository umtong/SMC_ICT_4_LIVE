"""Responsibility-separated control transfer for EasyChart RE1 reversals.

A trend-line and a channel edge are both diagonal price structures, but they do
not describe the same auction.  A trend-line is a one-dimensional crowding
boundary: once adverse aggression is absorbed and price crosses the midpoint of
the actual sweep, control may have transferred.  A channel edge belongs to a
bounded oscillation: the source requires a completed close back inside and, for
confirmation trading, the reclaimed edge must continue to hold on the next
five-minute decision bar.

The earlier flow implementation let the same one-minute absorption event execute
both structures.  That made descending-channel upper fades the dominant loss
family while wick trend-line reversals remained useful.  This module gives each
structure one causal responsibility without adding a fitted score or distance:

* isolated trend-line reversal: current adverse-flow absorption may replace a
  missing footprint only after the response closes through the midpoint of the
  original five-minute sweep range;
* any channel-member reversal: a full sweep/reclaim first waits for the next
  completed five-minute bar to remain inside; inherited visual/flow entry paths
  may act only after that hold state;
* horizontal and major-swing families, visual OB/FVG ownership, the independent
  flow-validated 15-minute OB family, stop, target, cost and account routing are
  unchanged.

The midpoint is event geometry, not a fitted return threshold.  The next-bar
channel hold is state hysteresis, not a timer selected from PnL.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, StructureFamily
from domain import Candle, Side
from easychart_re1_channel_rejection_hold import (
    CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE,
    HeldChannelRejectionMicroEngine,
)
from easychart_re1_flow import FlowSignal
from easychart_re1_flow_progress import ABSORPTION_MIDPOINT_PROGRESS_RULE
from easychart_re1_reversal_flow_ob import EasyChartRE1ReversalFlowOBBundle


RESPONSIBILITY_SEPARATED_TRANSFER_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "TREND_LINE_ABSORPTION_REQUIRES_SWEEP_MIDPOINT_CONTROL_TRANSFER_WHILE_CHANNEL_REJECTION_REQUIRES_NEXT_DECISION_BAR_HYSTERESIS"
)
if RESPONSIBILITY_SEPARATED_TRANSFER_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (RESPONSIBILITY_SEPARATED_TRANSFER_RULE,)


class TransferSeparatedMicroEngine(HeldChannelRejectionMicroEngine):
    """Use midpoint transfer for trend lines and next-bar hold for channels."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._transfer_counts: dict[str, int] = {}

    def _tinc(self, key: str) -> None:
        self._transfer_counts[key] = self._transfer_counts.get(key, 0) + 1

    def _interaction_bar(self, setup: ScenarioSetup) -> Candle | None:
        index = setup.interaction_index
        if 0 <= index < len(self.decision_bars):
            item = self.decision_bars[index]
            if item.ts_close_ns == setup.interaction_time_ns:
                return item
        return next(
            (
                item
                for item in reversed(self.decision_bars)
                if item.ts_close_ns == setup.interaction_time_ns
            ),
            None,
        )

    @staticmethod
    def _families(setup: ScenarioSetup) -> set[StructureFamily]:
        members = tuple(getattr(setup, "context_members", ()))
        if not members:
            members = (setup.context,)
        return {member.family for member in members}

    def _flow_signal(
        self,
        setup: ScenarioSetup,
        bar: Any,
        observation: Any,
    ) -> FlowSignal | None:
        signal = super()._flow_signal(setup, bar, observation)
        if signal is None or setup.path is ScenarioPath.ACCEPTANCE:
            return signal
        if "ABSORPTION" not in signal.mechanism:
            return signal

        families = self._families(setup)
        if StructureFamily.CHANNEL in families:
            # ``HeldChannelRejectionMicroEngine`` already makes a full channel
            # rejection wait for the next completed five-minute inside close.
            # Do not impose trend-line midpoint semantics on that later state.
            self._tinc("channel_absorption_routed_by_next_decision_hold")
            return signal

        if StructureFamily.TREND_LINE not in families:
            self._tinc("non_diagonal_absorption_inherited")
            return signal

        interaction = self._interaction_bar(setup)
        if interaction is None:
            raise RuntimeError("trend-line absorption lost its decision sweep bar")
        midpoint = (interaction.high + interaction.low) / 2.0
        progressed = (
            bar.close > midpoint
            if setup.side is Side.LONG
            else bar.close < midpoint
        )
        if progressed:
            self._tinc("trend_line_absorption_crossed_sweep_midpoint")
            self._trace(
                "trend_line_absorption_crossed_sweep_midpoint",
                bar.ts_close_ns,
                setup,
                interaction_midpoint=midpoint,
                response_close=bar.close,
                flow_kind=signal.kind.value,
                flow_mechanism=signal.mechanism,
                structure_families=sorted(family.value for family in families),
                rule_provenance=(
                    ABSORPTION_MIDPOINT_PROGRESS_RULE,
                    RESPONSIBILITY_SEPARATED_TRANSFER_RULE,
                ),
            )
            return signal

        self._tinc("trend_line_absorption_without_control_transfer_deferred")
        self._trace(
            "trend_line_absorption_without_control_transfer_deferred",
            bar.ts_close_ns,
            setup,
            interaction_midpoint=midpoint,
            response_close=bar.close,
            flow_kind=signal.kind.value,
            flow_mechanism=signal.mechanism,
            structure_families=sorted(family.value for family in families),
            rule_provenance=(
                ABSORPTION_MIDPOINT_PROGRESS_RULE,
                RESPONSIBILITY_SEPARATED_TRANSFER_RULE,
            ),
        )
        # A weak flow event does not kill the setup.  It simply declines to
        # replace the missing visual footprint, which may still form later.
        return None

    @property
    def transfer_policy_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._transfer_counts.items())),
            "trend_line": "ADVERSE_ABSORPTION_PLUS_ORIGINAL_5M_SWEEP_MIDPOINT_TRANSFER",
            "channel": "FULL_RECLAIM_PLUS_NEXT_COMPLETED_5M_INSIDE_HOLD",
            "rules": (
                ABSORPTION_MIDPOINT_PROGRESS_RULE,
                CHANNEL_REJECTION_NEXT_BAR_HOLD_RULE,
                RESPONSIBILITY_SEPARATED_TRANSFER_RULE,
            ),
        }


class EasyChartRE1TransferPolicyBundle(EasyChartRE1ReversalFlowOBBundle):
    """One account stream with structure-specific reversal evidence."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = TransferSeparatedMicroEngine(
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
        output["responsibility_separated_control_transfer"] = (
            self.micro.transfer_policy_diagnostics
        )
        return output


MultiScaleScenarioBundle = EasyChartRE1TransferPolicyBundle
