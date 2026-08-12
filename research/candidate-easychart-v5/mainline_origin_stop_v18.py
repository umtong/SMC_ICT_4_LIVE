"""Causal stop correction for channel main-line reversals.

A channel contains a main trend line and a parallel subline.  Breaking the
subline in the channel direction is continuation; breaking the main trend line
is a trend reversal.  The supplied trend-line rules place a breakout-and-retest
stop at the origin of the breakout wave, while the channel continuation rule
uses re-entry through the retested boundary.

The prior machine policy used the narrow channel-edge stop for both states.
This module uses the already observable causal swing origin for main-line
reversals and preserves the existing stop for subline continuation.  Entry,
target, risk, full-position management and all fixed account rules are
unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioPath, ScenarioSetup
from domain import Side
from repeated_horizontal_v17 import (
    MicroRepeatedHorizontalBundleV17,
    RepeatedHorizontalScenarioEngine,
)


MAINLINE_ORIGIN_STOP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CHANNEL_MAIN_TRENDLINE_REVERSAL_STOP_USES_BREAKOUT_WAVE_ORIGIN"
)
if MAINLINE_ORIGIN_STOP_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (MAINLINE_ORIGIN_STOP_RULE,)


MAIN_LINE_KINDS = {
    ObjectKind.ASCENDING_CHANNEL_LOWER,
    ObjectKind.DESCENDING_CHANNEL_UPPER,
}


class MainLineOriginStopScenarioEngine(RepeatedHorizontalScenarioEngine):
    """Separate reversal invalidation from channel-direction continuation."""

    def _acceptance_stop(self, setup: ScenarioSetup, time_ns: int) -> float | None:
        is_main_line_reversal = (
            setup.path is ScenarioPath.ACCEPTANCE
            and any(member.kind in MAIN_LINE_KINDS for member in setup.context_members)
        )
        if not is_main_line_reversal:
            return super()._acceptance_stop(setup, time_ns)

        origin = setup.acceptance_origin
        if origin is None:
            return None
        structural_stop = (
            origin.price - self.tick_size
            if setup.side is Side.LONG
            else origin.price + self.tick_size
        )
        bar = self._current_trigger_bar
        if bar is None or bar.ts_close_ns != time_ns:
            raise RuntimeError("main-line reversal stop requested without completed retest bar")
        executable_stop = (
            min(structural_stop, bar.low - self.tick_size)
            if setup.side is Side.LONG
            else max(structural_stop, bar.high + self.tick_size)
        )
        self._inc("channel_mainline_reversal_origin_stop")
        self._trace(
            "channel_mainline_reversal_origin_stop",
            time_ns,
            setup,
            origin_pivot_id=origin.pivot_id,
            origin_price=origin.price,
            retest_bar_low=bar.low,
            retest_bar_high=bar.high,
            executable_stop=executable_stop,
            provenance=MAINLINE_ORIGIN_STOP_RULE,
        )
        return executable_stop


class MicroMainLineOriginStopBundleV18(MicroRepeatedHorizontalBundleV17):
    """Integrated micro policy with state-specific channel invalidation."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = MainLineOriginStopScenarioEngine(
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
        output["channel_mainline_reversal_stop_policy"] = {
            "name": "BREAKOUT_WAVE_ORIGIN",
            "rule_provenance": MAINLINE_ORIGIN_STOP_RULE,
        }
        return output
