"""Ordered channel phase with a larger 60-minute structure router.

The base RE1 router changes direction whenever a close breaks a confirmed
five-hour (span-2) 60-minute wick swing.  The source calls for meaningful
higher-timeframe highs and lows, while lower-timeframe structure supplies the
entry.  This diagnostic variant retains the ordered channel-phase correction
and routes direction from the already-detected span-6 60-minute swings instead.
It changes no local scenario, target, stop, risk, cost or execution rule.
"""
from __future__ import annotations

import contracts_v5 as _contracts
from easychart_re1_phase import EasyChartRE1PhaseBundle


MACRO_MAJOR_SWING_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "SIXTY_MINUTE_DIRECTION_USES_CONFIRMED_SPAN_SIX_WICK_SWING_BREAK"
)
if MACRO_MAJOR_SWING_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MACRO_MAJOR_SWING_RULE,)


class EasyChartRE1PhaseMacro6Bundle(EasyChartRE1PhaseBundle):
    """Phase-corrected candidate routed by larger 60-minute swings."""

    DIRECTION_PIVOT_SPAN = 6

    @property
    def diagnostics(self):  # type: ignore[no-untyped-def]
        output = dict(super().diagnostics)
        output["top_down_context_router"] = dict(output["top_down_context_router"])
        output["top_down_context_router"].update(
            {
                "direction_pivot_span": self.DIRECTION_PIVOT_SPAN,
                "major_swing_rule": MACRO_MAJOR_SWING_RULE,
            },
        )
        return output


MultiScaleScenarioBundle = EasyChartRE1PhaseMacro6Bundle
