"""Unexpected-flow and realized-impact higher-timeframe acceptance relay."""

from __future__ import annotations

from dataclasses import replace
from statistics import median
from typing import Any, Mapping

from adaptive_fresh_hierarchical_engine import AdaptiveFreshHierarchicalEngine
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition
from surprise_impact_core import SurpriseImpactAssessment, assess_surprise_impact


class SurpriseImpactHierarchicalEngine(AdaptiveFreshHierarchicalEngine):
    """Replace absolute HTF activity quality with surprise-impact causality.

    The downstream HML scenario is unchanged.  A completed 60-minute structural
    break may create context only when aggressive flow is unexpected relative to
    prior completed auctions and, when enabled, that surprise actually converts
    into direction-consistent displacement rather than being absorbed.

    AFHR's prior range/volume quality filter is intentionally disabled.  Its
    completed-close freshness invalidation is retained as the one valid context
    control learned from the failed AFHR candidate.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._siar_by_context: dict[str, dict[str, Any]] = {}
        self._last_siar_assessment: SurpriseImpactAssessment | None = None
        self._siar_preapproved_direction: str | None = None

    def _quality_enabled(self) -> bool:
        # The old absolute range/volume quality definition is replaced, not
        # stacked with the new hypothesis.
        return False

    def _surprise_enabled(self) -> bool:
        return bool(self.params.get("siar_use_flow_surprise", True))

    def _impact_enabled(self) -> bool:
        return bool(self.params.get("siar_use_impact_efficiency", True))

    def _baseline_acceptance_direction(self, bar: _AuctionBar) -> str | None:
        if self._siar_preapproved_direction is not None:
            return self._siar_preapproved_direction
        return super()._baseline_acceptance_direction(bar)

    def _assess_surprise_impact(
        self,
        bar: _AuctionBar,
        direction: str,
    ) -> SurpriseImpactAssessment:
        atr_bars = int(self.params.get("hsc_bias_atr_bars", 12))
        volume_bars = int(self.params.get("hsc_bias_volume_bars", 12))
        if len(self._bias_true_ranges) < atr_bars or len(self._bias_volumes) < volume_bars:
            return assess_surprise_impact(
                prior_flow_intensity=[],
                prior_signed_displacement_atr=[],
                current_flow_intensity=0.0,
                current_directional_displacement_atr=0.0,
                direction=direction,
                use_surprise=self._surprise_enabled(),
                use_impact_efficiency=self._impact_enabled(),
                lookback=int(self.params.get("siar_flow_lookback", 24)),
                minimum_history=int(self.params.get("siar_min_history", 12)),
                flow_quantile=float(self.params.get("siar_surprise_quantile", 0.75)),
                minimum_efficiency_history=int(self.params.get("siar_min_efficiency_history", 4)),
            )

        atr_htf = sum(self._bias_true_ranges[-atr_bars:]) / atr_bars
        baseline_volume = median(self._bias_volumes[-volume_bars:])
        if atr_htf <= 0.0 or baseline_volume <= 0.0:
            return assess_surprise_impact(
                prior_flow_intensity=[],
                prior_signed_displacement_atr=[],
                current_flow_intensity=0.0,
                current_directional_displacement_atr=0.0,
                direction=direction,
                use_surprise=self._surprise_enabled(),
                use_impact_efficiency=self._impact_enabled(),
                lookback=int(self.params.get("siar_flow_lookback", 24)),
                minimum_history=int(self.params.get("siar_min_history", 12)),
                flow_quantile=float(self.params.get("siar_surprise_quantile", 0.75)),
                minimum_efficiency_history=int(self.params.get("siar_min_efficiency_history", 4)),
            )

        lookback = int(self.params.get("siar_flow_lookback", 24))
        history = self._bias_history[-lookback:]
        prior_flow = [
            (2.0 * value.taker_buy_volume - value.volume) / baseline_volume
            for value in history
        ]
        prior_displacement = [
            (value.close - value.open) / atr_htf
            for value in history
        ]
        current_flow = (2.0 * bar.taker_buy_volume - bar.volume) / baseline_volume
        sign = 1.0 if direction == "LONG" else -1.0
        current_directional_displacement = sign * (bar.close - bar.open) / atr_htf
        return assess_surprise_impact(
            prior_flow_intensity=prior_flow,
            prior_signed_displacement_atr=prior_displacement,
            current_flow_intensity=current_flow,
            current_directional_displacement_atr=current_directional_displacement,
            direction=direction,
            use_surprise=self._surprise_enabled(),
            use_impact_efficiency=self._impact_enabled(),
            lookback=lookback,
            minimum_history=int(self.params.get("siar_min_history", 12)),
            flow_quantile=float(self.params.get("siar_surprise_quantile", 0.75)),
            minimum_efficiency_history=int(self.params.get("siar_min_efficiency_history", 4)),
        )

    @staticmethod
    def _assessment_transition(
        bar: _AuctionBar,
        direction: str,
        assessment: SurpriseImpactAssessment,
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=f"SIAR-ASSESSMENT-{bar.end_ts_ns}",
            event_type="SIAR_ACCEPTANCE_TRANSITION",
            previous_state="IDLE",
            next_state="RESET",
            reason_code=assessment.classification,
            reference_price=bar.close,
            details={
                "direction": direction,
                "baseline_precondition": "COMPLETED_HIGHER_TIMEFRAME_RANGE_ACCEPTED",
                **assessment.details(),
            },
        )

    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        baseline_direction = AdaptiveFreshHierarchicalEngine._baseline_acceptance_direction(self, bar)
        if baseline_direction is None:
            return ()

        assessment = self._assess_surprise_impact(bar, baseline_direction)
        self._last_siar_assessment = assessment
        if not assessment.ready or not assessment.passed:
            return (self._assessment_transition(bar, baseline_direction, assessment),)

        previous_context = self._bias.context_id if self._bias is not None else None
        self._siar_preapproved_direction = baseline_direction
        try:
            transitions = super()._evaluate_completed_bias(bar, snapshot)
        finally:
            self._siar_preapproved_direction = None

        bias = self._bias
        if bias is not None and bias.context_id != previous_context:
            self._siar_by_context = {bias.context_id: assessment.details()}
        return transitions

    def _clear_context(self, context_id: str | None) -> None:
        super()._clear_context(context_id)
        if context_id is not None:
            self._siar_by_context.pop(context_id, None)

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        contract = self._siar_by_context.get(bias.context_id, {})
        step = super()._emit(snapshot, bias, sweep)
        if step.signal is None:
            return step
        details = {
            **dict(step.signal.details),
            "surprise_impact_contract": contract,
            "siar_ablation_contract": {
                "flow_surprise": self._surprise_enabled(),
                "impact_efficiency": self._impact_enabled(),
                "completed_close_freshness": self._freshness_enabled(),
            },
        }
        signal: ScenarioSignal = replace(step.signal, family="SIAR", details=details)
        return ScenarioStep(transitions=step.transitions, signal=signal)
