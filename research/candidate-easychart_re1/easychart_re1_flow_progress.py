"""Bind reversal absorption to material price progress through auction midpoint.

Large adverse taker flow with little further penetration identifies possible
absorption, but it does not prove that passive liquidity has won.  The first
machine implementation entered as soon as price reclaimed the projected line by
one tick.  In strong continuation moves this was merely a pause near the edge.

For a flow-substituted rejection/bounce/rotation entry, this module requires the
completed one-minute response to have crossed the 50% midpoint of the original
five-minute interaction range in the intended direction.  The midpoint is the
natural balance point of the actual sweep auction, not a fitted return or
volatility threshold.  When midpoint progress is absent, flow simply declines
to substitute for the missing footprint; the ordinary visual OB/FVG path remains
alive and may enter later.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup
from domain import Candle, Side
from easychart_re1_flow import FlowSignal
from easychart_re1_flow_ob import (
    FlowValidatedOrderBlockDecisionStructureBook,
)
from easychart_re1_flow_ob_responsibility import (
    EasyChartRE1ResponsibleFlowOBBundle,
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
    ResponsiblePhaseFlowMicroEngine,
)
from easychart_re1_reversal_flow_ob import (
    ReversalOnlyResponsiblePhaseFlowMicroEngine,
)


ABSORPTION_MIDPOINT_PROGRESS_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ADVERSE_TAKER_ABSORPTION_REPLACES_A_MISSING_REVERSAL_FOOTPRINT_ONLY_AFTER_PRICE_RECLAIMS_THE_MIDPOINT_OF_THE_ORIGINAL_DECISION_SWEEP_RANGE"
)
if ABSORPTION_MIDPOINT_PROGRESS_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (ABSORPTION_MIDPOINT_PROGRESS_RULE,)


class AbsorptionMidpointProgressMixin:
    """Keep weak flow observational while preserving every visual entry path."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._progress_counts: dict[str, int] = {}

    def _pinc(self, key: str) -> None:
        self._progress_counts[key] = self._progress_counts.get(key, 0) + 1

    def _interaction_bar(self, setup: ScenarioSetup) -> Candle | None:
        index = setup.interaction_index
        if 0 <= index < len(self.decision_bars):
            bar = self.decision_bars[index]
            if bar.ts_close_ns == setup.interaction_time_ns:
                return bar
        return next(
            (
                bar
                for bar in reversed(self.decision_bars)
                if bar.ts_close_ns == setup.interaction_time_ns
            ),
            None,
        )

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
        interaction = self._interaction_bar(setup)
        if interaction is None:
            raise RuntimeError("flow absorption lost its original decision bar")
        midpoint = (interaction.high + interaction.low) / 2.0
        progressed = (
            bar.close > midpoint
            if setup.side is Side.LONG
            else bar.close < midpoint
        )
        if progressed:
            self._pinc("absorption_crossed_interaction_midpoint")
            self._trace(
                "absorption_crossed_interaction_midpoint",
                bar.ts_close_ns,
                setup,
                interaction_midpoint=midpoint,
                response_close=bar.close,
                flow_kind=signal.kind.value,
                flow_mechanism=signal.mechanism,
                rule_provenance=ABSORPTION_MIDPOINT_PROGRESS_RULE,
            )
            return signal
        self._pinc("absorption_without_midpoint_progress_left_for_visual_entry")
        self._trace(
            "absorption_without_midpoint_progress_left_for_visual_entry",
            bar.ts_close_ns,
            setup,
            interaction_midpoint=midpoint,
            response_close=bar.close,
            flow_kind=signal.kind.value,
            flow_mechanism=signal.mechanism,
            rule_provenance=ABSORPTION_MIDPOINT_PROGRESS_RULE,
        )
        return None

    @property
    def absorption_progress_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._progress_counts.items())),
            "rule_provenance": ABSORPTION_MIDPOINT_PROGRESS_RULE,
        }


class ProgressReversalMicroEngine(
    AbsorptionMidpointProgressMixin,
    ReversalOnlyResponsiblePhaseFlowMicroEngine,
):
    pass


class ProgressMajorSwingEngine(
    AbsorptionMidpointProgressMixin,
    ResponsibleFlowMajorSwingEngine,
):
    pass


class ProgressFlowDecisionAreaEngine(
    AbsorptionMidpointProgressMixin,
    ResponsibleFlowValidatedDecisionAreaEngine,
):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = FlowValidatedOrderBlockDecisionStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )


class EasyChartRE1FlowProgressBundle(EasyChartRE1ResponsibleFlowOBBundle):
    """Reversal/OB account where absorption and price progress are one event."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = ProgressReversalMicroEngine(
            symbol, tick_size, scale_name="MICRO", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, **kwargs,
        )
        self.major_swing = ProgressMajorSwingEngine(
            symbol, tick_size, scale_name="LIQUIDITY", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, **kwargs,
        )
        self.flow_decision_ob = ProgressFlowDecisionAreaEngine(
            symbol, tick_size, scale_name="FLOW_DECISION_OB", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["absorption_midpoint_progress"] = {
            "micro": self.micro.absorption_progress_diagnostics,
            "major_swing": self.major_swing.absorption_progress_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.absorption_progress_diagnostics,
            "rule_provenance": ABSORPTION_MIDPOINT_PROGRESS_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FlowProgressBundle
