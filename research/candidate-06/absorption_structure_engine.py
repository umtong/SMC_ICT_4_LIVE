"""Absorption event followed by independently confirmed opposite structure."""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Any, Mapping

from adaptive_fresh_core import DirectionalFreshnessClock
from adaptive_fresh_hierarchical_engine import AdaptiveFreshHierarchicalEngine
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition
from surprise_impact_hierarchical_engine import SurpriseImpactHierarchicalEngine


@dataclass(slots=True)
class _AbsorptionAnchor:
    anchor_id: str
    source_direction: str
    reversal_direction: str
    source_end_ts_ns: int
    armed_index: int
    expires_index: int
    open: float
    high: float
    low: float
    close: float
    atr_htf: float
    range_atr: float
    body_fraction: float
    flow_ratio: float
    relative_volume: float
    assessment: dict[str, Any]


class AbsorptionConfirmedStructureReversalEngine(SurpriseImpactHierarchicalEngine):
    """Trade only after inefficient HTF aggression is followed by opposite CHoCH.

    An accepted higher-timeframe breakout is not faded on the event bar. When
    direction-aligned aggressive flow produces sub-reference displacement, the
    completed auction becomes an absorption anchor. A later completed 5-minute
    auction must close through prior 5-minute structure in the opposite direction
    with displacement, range and (when enabled) signed-flow agreement. Only then
    is an opposite directional context created. The inherited confirmed
    swing/equal-liquidity sweep, separate one-minute response, structural target,
    delayed entry, Nautilus execution and current-NAV risk sizing are unchanged.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._absorption_anchor: _AbsorptionAnchor | None = None
        self._acsr_by_context: dict[str, dict[str, Any]] = {}
        self._acsr_sequence = 0

    def _require_absorption(self) -> bool:
        return bool(self.params.get("acsr_require_impact_absorption", True))

    def _structure_flow_enabled(self) -> bool:
        return bool(self.params.get("acsr_use_structure_flow", True))

    @staticmethod
    def _anchor_transition(
        *,
        scenario_id: str,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=scenario_id,
            event_type="ACSR_ANCHOR_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )

    def _clear_context(self, context_id: str | None) -> None:
        super()._clear_context(context_id)
        if context_id is not None:
            self._acsr_by_context.pop(context_id, None)

    def _reset_active_context_for_anchor(
        self,
        reference_price: float,
    ) -> list[ScenarioTransition]:
        transitions: list[ScenarioTransition] = []
        if self._sweep is not None:
            transitions.append(
                self._sweep_transition(
                    self._sweep,
                    self._sweep.state,
                    "RESET",
                    "NEW_ABSORPTION_ANCHOR_REPLACES_ACTIVE_SWEEP",
                    reference_price,
                    {},
                ),
            )
            self._sweep = None
        if self._bias is not None:
            context_id = self._bias.context_id
            transitions.append(
                self._bias_transition(
                    self._bias,
                    "BIAS_ACTIVE",
                    "RESET",
                    "NEW_ABSORPTION_ANCHOR_REPLACES_ACTIVE_CONTEXT",
                    reference_price,
                    {},
                ),
            )
            self._bias = None
            self._clear_context(context_id)
        return transitions

    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        direction = AdaptiveFreshHierarchicalEngine._baseline_acceptance_direction(self, bar)
        if direction is None:
            return ()

        assessment = self._assess_surprise_impact(bar, direction)
        self._last_siar_assessment = assessment
        assessment_details = assessment.details()
        common = {
            "source_direction": direction,
            "reversal_direction": "SHORT" if direction == "LONG" else "LONG",
            "baseline_precondition": "COMPLETED_HIGHER_TIMEFRAME_RANGE_ACCEPTED",
            **assessment_details,
        }
        assessment_transition = self._anchor_transition(
            scenario_id=f"ACSR-ASSESSMENT-{bar.end_ts_ns}",
            previous_state="IDLE",
            next_state="RESET",
            reason=assessment.classification,
            reference_price=bar.close,
            details=common,
        )
        if not assessment.ready:
            return (assessment_transition,)

        is_absorption = assessment.classification == "FLOW_SURPRISE_ABSORBED_WITH_WEAK_PRICE_RESPONSE"
        if self._require_absorption() and not is_absorption:
            reason = (
                "EFFICIENT_BREAKOUT_NOT_A_REVERSAL_ANCHOR"
                if assessment.passed
                else assessment.classification
            )
            return (
                replace(
                    assessment_transition,
                    reason_code=reason,
                    details={**common, "anchor_armed": False},
                ),
            )

        atr_bars = int(self.params.get("hsc_bias_atr_bars", 12))
        volume_bars = int(self.params.get("hsc_bias_volume_bars", 12))
        atr_htf = sum(self._bias_true_ranges[-atr_bars:]) / atr_bars
        baseline_volume = median(self._bias_volumes[-volume_bars:])
        if atr_htf <= 0.0 or baseline_volume <= 0.0:
            return (
                replace(
                    assessment_transition,
                    reason_code="ACSR_ANCHOR_REFERENCE_NOT_READY",
                    details={**common, "anchor_armed": False},
                ),
            )

        transitions = self._reset_active_context_for_anchor(bar.close)
        if self._absorption_anchor is not None:
            old = self._absorption_anchor
            transitions.append(
                self._anchor_transition(
                    scenario_id=old.anchor_id,
                    previous_state="ABSORPTION_ARMED",
                    next_state="RESET",
                    reason="NEWER_ABSORPTION_ANCHOR_REPLACED_PENDING_ANCHOR",
                    reference_price=bar.close,
                    details={"replacement_source_end_ts_ns": bar.end_ts_ns},
                ),
            )

        self._acsr_sequence += 1
        confirmation_periods = float(self.params.get("acsr_confirmation_periods", 2.0))
        maximum_age = max(1, int(round(self._bias_period * confirmation_periods)))
        self._absorption_anchor = _AbsorptionAnchor(
            anchor_id=f"ACSR-ANCHOR-{bar.end_ts_ns}-{self._acsr_sequence:06d}",
            source_direction=direction,
            reversal_direction="SHORT" if direction == "LONG" else "LONG",
            source_end_ts_ns=bar.end_ts_ns,
            armed_index=snapshot.index,
            expires_index=snapshot.index + maximum_age,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            atr_htf=atr_htf,
            range_atr=bar.candle_range / atr_htf,
            body_fraction=bar.body_fraction,
            flow_ratio=bar.flow_ratio,
            relative_volume=bar.volume / baseline_volume,
            assessment=assessment_details,
        )
        transitions.append(
            self._anchor_transition(
                scenario_id=self._absorption_anchor.anchor_id,
                previous_state="IDLE",
                next_state="ABSORPTION_ARMED",
                reason=(
                    "IMPACT_INEFFICIENT_BREAKOUT_ARMED_FOR_OPPOSITE_STRUCTURE"
                    if is_absorption
                    else "BASELINE_BREAKOUT_ARMED_WITHOUT_IMPACT_CLASSIFICATION"
                ),
                reference_price=bar.close,
                details={
                    **common,
                    "anchor_armed": True,
                    "source_end_ts_ns": bar.end_ts_ns,
                    "source_high": bar.high,
                    "source_low": bar.low,
                    "source_close": bar.close,
                    "atr_htf": atr_htf,
                    "maximum_confirmation_age_bars": maximum_age,
                },
            ),
        )
        return tuple(transitions)

    def _reset_anchor(
        self,
        *,
        reason: str,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> ScenarioTransition | None:
        anchor = self._absorption_anchor
        if anchor is None:
            return None
        transition = self._anchor_transition(
            scenario_id=anchor.anchor_id,
            previous_state="ABSORPTION_ARMED",
            next_state="RESET",
            reason=reason,
            reference_price=reference_price,
            details=details,
        )
        self._absorption_anchor = None
        return transition

    def _evaluate_completed_reversal_structure(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        anchor = self._absorption_anchor
        if anchor is None:
            return ()
        if bar.end_ts_ns <= anchor.source_end_ts_ns:
            return ()
        if snapshot.index > anchor.expires_index:
            transition = self._reset_anchor(
                reason="ABSORPTION_ANCHOR_EXPIRED_WITHOUT_OPPOSITE_STRUCTURE",
                reference_price=bar.close,
                details={
                    "age_bars": snapshot.index - anchor.armed_index,
                    "maximum_age_bars": anchor.expires_index - anchor.armed_index,
                },
            )
            return () if transition is None else (transition,)

        extension = float(self.params.get("acsr_disproof_extension_atr_htf", 0.02)) * anchor.atr_htf
        disproved = (
            bar.close > anchor.high + extension
            if anchor.source_direction == "LONG"
            else bar.close < anchor.low - extension
        )
        if disproved:
            transition = self._reset_anchor(
                reason="ABSORPTION_DISPROVED_BY_DIRECTIONAL_ACCEPTANCE",
                reference_price=bar.close,
                details={
                    "source_direction": anchor.source_direction,
                    "source_high": anchor.high,
                    "source_low": anchor.low,
                    "disproof_extension": extension,
                },
            )
            return () if transition is None else (transition,)

        lookback = int(self.params.get("acsr_structure_lookback_bars", 4))
        range_lookback = int(self.params.get("acsr_structure_range_lookback", 8))
        if lookback <= 0 or range_lookback <= 0:
            raise ValueError("ACSR structure lookbacks must be positive")
        if len(self._liquidity_history) < max(lookback, 2):
            return ()
        prior = self._liquidity_history[-lookback:]
        reference_ranges = [value.candle_range for value in self._liquidity_history[-range_lookback:]]
        baseline_range = median(reference_ranges) if reference_ranges else 0.0
        if baseline_range <= 0.0 or bar.candle_range <= 0.0:
            return ()

        prior_high = max(value.high for value in prior)
        prior_low = min(value.low for value in prior)
        break_buffer = float(self.params.get("acsr_structure_break_range_fraction", 0.05)) * baseline_range
        minimum_body = float(self.params.get("acsr_structure_body_fraction", 0.50))
        minimum_range = float(self.params.get("acsr_structure_relative_range", 0.80))
        minimum_flow = float(self.params.get("acsr_structure_flow_ratio", 0.04))
        outer_close = float(self.params.get("acsr_structure_close_location", 0.65))
        relative_range = bar.candle_range / baseline_range

        if anchor.reversal_direction == "SHORT":
            structure_level = prior_low
            price_confirmed = (
                bar.close < bar.open
                and bar.close < structure_level - break_buffer
                and bar.body_fraction >= minimum_body
                and relative_range >= minimum_range
                and bar.close_location <= 1.0 - outer_close
            )
            flow_confirmed = bar.flow_ratio <= -minimum_flow
        else:
            structure_level = prior_high
            price_confirmed = (
                bar.close > bar.open
                and bar.close > structure_level + break_buffer
                and bar.body_fraction >= minimum_body
                and relative_range >= minimum_range
                and bar.close_location >= outer_close
            )
            flow_confirmed = bar.flow_ratio >= minimum_flow
        if not price_confirmed or (self._structure_flow_enabled() and not flow_confirmed):
            return ()

        self._bias_sequence += 1
        direction = anchor.reversal_direction
        # Preserve one causal state chain from ABSORPTION_ARMED into BIAS_ACTIVE.
        # The event recorder keys state by scenario_id, so a new ID here would
        # incorrectly make the transition appear to start from IDLE.
        context_id = anchor.anchor_id
        relative_volume = 1.0
        if self._liquidity_history:
            volumes = [value.volume for value in self._liquidity_history[-range_lookback:]]
            baseline_volume = median(volumes) if volumes else 0.0
            if baseline_volume > 0.0:
                relative_volume = bar.volume / baseline_volume
        lifetime = float(self.params.get("hsc_bias_lifetime_periods", 3.0))
        self._bias = _Bias(
            context_id=context_id,
            direction=direction,
            boundary=structure_level,
            origin=anchor.close,
            high=max(anchor.high, bar.high),
            low=min(anchor.low, bar.low),
            close=bar.close,
            extreme=bar.high if direction == "LONG" else bar.low,
            atr_htf=anchor.atr_htf,
            created_index=snapshot.index,
            expires_index=snapshot.index + max(1, int(self._bias_period * lifetime)),
            range_atr=bar.candle_range / anchor.atr_htf,
            body_fraction=bar.body_fraction,
            flow_ratio=bar.flow_ratio,
            relative_volume=relative_volume,
        )
        contract = {
            "anchor_id": anchor.anchor_id,
            "source_direction": anchor.source_direction,
            "reversal_direction": direction,
            "source_end_ts_ns": anchor.source_end_ts_ns,
            "source_high": anchor.high,
            "source_low": anchor.low,
            "source_close": anchor.close,
            "absorption_assessment": anchor.assessment,
            "structure_end_ts_ns": bar.end_ts_ns,
            "structure_level": structure_level,
            "structure_break_buffer": break_buffer,
            "structure_body_fraction": bar.body_fraction,
            "structure_relative_range": relative_range,
            "structure_flow_ratio": bar.flow_ratio,
            "structure_flow_required": self._structure_flow_enabled(),
        }
        self._acsr_by_context = {context_id: contract}
        self._freshness_by_context = {
            context_id: DirectionalFreshnessClock(
                direction=direction,
                last_close_extreme=bar.close,
                last_refresh_index=snapshot.index,
            ),
        }
        self._quality_by_context = {context_id: {"acsr_contract": contract}}
        self._absorption_anchor = None
        transition = self._anchor_transition(
            scenario_id=context_id,
            previous_state="ABSORPTION_ARMED",
            next_state="BIAS_ACTIVE",
            reason="ABSORPTION_FOLLOWED_BY_CONFIRMED_OPPOSITE_STRUCTURE_BREAK",
            reference_price=bar.close,
            details=contract,
        )
        return (transition,)

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None

        if self._bias is not None:
            context_step = self._advance_bias(snapshot)
            transitions.extend(context_step.transitions)
        if self._sweep is not None:
            sweep_step = self._advance_sweep(snapshot, allow_new=allow_new)
            transitions.extend(sweep_step.transitions)
            signal = sweep_step.signal
        if (
            signal is None
            and self._bias is not None
            and self._sweep is None
            and allow_new
            and snapshot.index >= self._cooldown_until
        ):
            started = self._maybe_start_sweep(snapshot)
            if started is not None:
                transitions.append(started)

        completed_bias = self._accumulate(snapshot, period=self._bias_period, kind="bias")
        completed_liquidity = self._accumulate(snapshot, period=self._liquidity_period, kind="liquidity")
        if completed_bias is not None:
            transitions.extend(self._evaluate_completed_bias(completed_bias, snapshot))
            self._append_bias_history(completed_bias)
        if completed_liquidity is not None:
            transitions.extend(self._evaluate_completed_reversal_structure(completed_liquidity, snapshot))
            self._liquidity_history.append(completed_liquidity)
            if len(self._liquidity_history) > 16:
                self._liquidity_history = self._liquidity_history[-16:]
            if completed_liquidity.end_ts_ns != self._last_pool_confirmation_bar_ns:
                self._confirm_liquidity_pools()
                self._last_pool_confirmation_bar_ns = completed_liquidity.end_ts_ns

        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions = list(super().abort_active(snapshot, reason).transitions)
        if self._absorption_anchor is not None:
            anchor = self._absorption_anchor
            transitions.append(
                self._anchor_transition(
                    scenario_id=anchor.anchor_id,
                    previous_state="ABSORPTION_ARMED",
                    next_state="RESET",
                    reason=reason,
                    reference_price=snapshot.observation.close,
                    details={"aborted": True},
                ),
            )
            self._absorption_anchor = None
        return ScenarioStep(transitions=tuple(transitions))

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        contract = self._acsr_by_context.get(bias.context_id, {})
        step = super()._emit(snapshot, bias, sweep)
        if step.signal is None:
            return step
        details = {
            **dict(step.signal.details),
            "absorption_structure_contract": contract,
            "acsr_ablation_contract": {
                "impact_absorption": self._require_absorption(),
                "opposite_structure_break": True,
                "structure_flow": self._structure_flow_enabled(),
                "sweep_flow": self._stage_flag("hff_use_sweep_flow"),
                "response_flow": self._stage_flag("hff_use_response_flow"),
            },
        }
        signal: ScenarioSignal = replace(step.signal, family="ACSR", details=details)
        return ScenarioStep(transitions=step.transitions, signal=signal)
