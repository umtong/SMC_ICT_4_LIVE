"""Focused causal-flow expansion for the natural EasyChart RE1 core.

Empirical diagnosis of the first flow pass showed a useful distinction:
current-bar absorption at a diagonal/channel boundary often represented a real
failed auction, while stale repeated absorption and flow substitution in generic
horizontal/decision/wedge families multiplied low-quality trades.

This candidate therefore gives order flow only two new responsibilities:

* MICRO diagonal/channel and major-swing reversals may enter on a current
  sweep-reclaim absorption bar;
* MICRO and major-swing accepted breaks may replace the next visual response on
  the first exact retest, but only after aligned break/hold flow.

Repeated absorption is retained as diagnostic evidence but cannot originate a
trade here.  Horizontal repeated-defense scenarios remain visual and unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_auction_flow import (
    AUCTION_CYCLE_FLOW_RULE,
    AuctionCycleFlowEntryMixin,
)
from easychart_re1_flow import FlowSignal, FlowTriggerKind
from easychart_re1_natural_geometry import (
    EasyChartRE1NaturalGeometryBundle,
    NaturalHorizontalEngine,
    NaturalMajorSwingEngine,
    NaturalMicroEngine,
)


FOCUSED_FLOW_RESPONSIBILITY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CURRENT_SWEEP_RECLAIM_ABSORPTION_EXPANDS_DIAGONAL_CHANNEL_AND_MAJOR_SWING_OPPORTUNITIES_WHILE_REPEATED_ABSORPTION_AND_GENERIC_HORIZONTAL_SUBSTITUTION_REMAIN_DIAGNOSTIC"
)
if FOCUSED_FLOW_RESPONSIBILITY_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (FOCUSED_FLOW_RESPONSIBILITY_RULE,)


_REPEATED = {
    FlowTriggerKind.REPEATED_SELL_ABSORPTION,
    FlowTriggerKind.REPEATED_BUY_ABSORPTION,
}


class FocusedAuctionFlowMixin(AuctionCycleFlowEntryMixin):
    """Reject stale/repeated absorption as an entry origin."""

    def _reversal_absorption_signal(self, setup: Any, bar: Any, observation: Any) -> FlowSignal | None:
        signal = super()._reversal_absorption_signal(setup, bar, observation)
        if signal is None:
            return None
        if signal.kind in _REPEATED:
            self._finc("repeated_absorption_kept_diagnostic_only")
            return None
        self._finc("current_sweep_reclaim_absorption_allowed")
        return signal

    @property
    def focused_flow_diagnostics(self) -> dict[str, Any]:
        return {
            "reversal_entry": "CURRENT_SWEEP_RECLAIM_ABSORPTION_ONLY",
            "acceptance_entry": "BREAK_HOLD_FLOW_PLUS_FIRST_EXACT_RETEST_RESPONSE",
            "repeated_absorption": "DIAGNOSTIC_ONLY",
            "rules": (
                AUCTION_CYCLE_FLOW_RULE,
                FOCUSED_FLOW_RESPONSIBILITY_RULE,
            ),
        }


class FocusedFlowMicroEngine(FocusedAuctionFlowMixin, NaturalMicroEngine):
    pass


class FocusedFlowMajorSwingEngine(FocusedAuctionFlowMixin, NaturalMajorSwingEngine):
    pass


class EasyChartRE1FocusedFlowBundle(EasyChartRE1NaturalGeometryBundle):
    """Natural visual core plus focused flow at causal liquidity boundaries."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = FocusedFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = NaturalHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = FocusedFlowMajorSwingEngine(
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
        output["focused_causal_flow"] = {
            "micro": self.micro.focused_flow_diagnostics,
            "horizontal": "VISUAL_ONLY",
            "major_swing": self.major_swing.focused_flow_diagnostics,
            "rule_provenance": FOCUSED_FLOW_RESPONSIBILITY_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FocusedFlowBundle
