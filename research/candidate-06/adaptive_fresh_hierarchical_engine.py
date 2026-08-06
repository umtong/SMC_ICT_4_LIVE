"""Adaptive-quality and progress-fresh hierarchical liquidity continuation."""

from __future__ import annotations

from dataclasses import replace
from statistics import median
from typing import Any, Mapping

from adaptive_fresh_core import (
    DirectionalFreshnessClock,
    QualityAssessment,
    assess_prior_only_quality,
)
from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


class AdaptiveFreshHierarchicalEngine(HierarchicalMultiLiquidityEngine):
    """Require exceptional HTF acceptance and continued directional progress.

    This is a controlled extension of HML.  Confirmed swing/equal pools, sweep
    and response sequence, structural targets/stops, delayed entry, risk sizing,
    fills and NAV accounting are unchanged.

    The extension changes only two causal claims:

    * ``adaptive quality``: a baseline-qualified completed HTF break must also
      rank in the configured upper prior-only range and volume distributions,
      with a directional body floor;
    * ``extreme freshness``: a structurally unbroken bias is no longer treated
      as active forever.  Its clock refreshes only on a new completed close in
      the accepted direction.  A stale context and any armed sweep are reset.

    Both mechanisms are independently switchable for one-variable ablations.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._freshness_by_context: dict[str, DirectionalFreshnessClock] = {}
        self._quality_by_context: dict[str, dict[str, Any]] = {}
        self._last_quality_assessment: QualityAssessment | None = None

    def _quality_enabled(self) -> bool:
        return bool(self.params.get("afhr_use_adaptive_quality", True))

    def _freshness_enabled(self) -> bool:
        return bool(self.params.get("afhr_use_extreme_freshness", True))

    def _bias_flow_enabled(self) -> bool:
        # HML inherits the HFF stage contract.  Falling back to the legacy flag
        # preserves exact behavior for configs created before stage factorization.
        stage = getattr(self, "_stage_flag", None)
        if callable(stage):
            return bool(stage("hff_use_bias_flow"))
        return bool(self.params.get("hsc_use_flow_proxy", True))

    def _baseline_acceptance_direction(
        self,
        bar: _AuctionBar,
    ) -> str | None:
        """Mirror the parent precondition without mutating bias/sweep state."""

        atr_bars = int(self.params.get("hsc_bias_atr_bars", 12))
        volume_bars = int(self.params.get("hsc_bias_volume_bars", 12))
        lookback = int(self.params.get("hsc_bias_breakout_lookback", 4))
        required = max(atr_bars, volume_bars, lookback)
        if len(self._bias_history) < required:
            return None

        atr_htf = sum(self._bias_true_ranges[-atr_bars:]) / atr_bars
        baseline_volume = median(self._bias_volumes[-volume_bars:])
        if atr_htf <= 0.0 or baseline_volume <= 0.0 or bar.candle_range <= 0.0:
            return None

        prior = self._bias_history[-lookback:]
        prior_high = max(value.high for value in prior)
        prior_low = min(value.low for value in prior)
        acceptance = float(self.params.get("hsc_bias_acceptance_close_atr", 0.02)) * atr_htf
        range_atr = bar.candle_range / atr_htf
        relative_volume = bar.volume / baseline_volume
        minimum_range = float(self.params.get("hsc_bias_range_atr", 0.75))
        minimum_body = float(self.params.get("hsc_bias_body_fraction", 0.50))
        minimum_volume = float(self.params.get("hsc_bias_relative_volume", 0.95))
        minimum_flow = float(self.params.get("hsc_bias_flow_ratio", 0.04))
        outer_close = float(self.params.get("hsc_bias_close_location", 0.68))
        use_flow = self._bias_flow_enabled()

        if (
            bar.close > prior_high + acceptance
            and bar.close > bar.open
            and range_atr >= minimum_range
            and bar.body_fraction >= minimum_body
            and relative_volume >= minimum_volume
            and ((bar.flow_ratio >= minimum_flow) if use_flow else True)
            and bar.close_location >= outer_close
        ):
            return "LONG"
        if (
            bar.close < prior_low - acceptance
            and bar.close < bar.open
            and range_atr >= minimum_range
            and bar.body_fraction >= minimum_body
            and relative_volume >= minimum_volume
            and ((bar.flow_ratio <= -minimum_flow) if use_flow else True)
            and bar.close_location <= 1.0 - outer_close
        ):
            return "SHORT"
        return None

    def _assess_quality(self, bar: _AuctionBar) -> QualityAssessment:
        return assess_prior_only_quality(
            prior_ranges=[value.candle_range for value in self._bias_history],
            prior_volumes=self._bias_volumes,
            current_range=bar.candle_range,
            current_volume=bar.volume,
            current_body_fraction=bar.body_fraction,
            enabled=self._quality_enabled(),
            lookback=int(self.params.get("afhr_quality_lookback", 24)),
            minimum_history=int(self.params.get("afhr_quality_min_history", 12)),
            quantile=float(self.params.get("afhr_quality_quantile", 0.75)),
            body_floor=float(self.params.get("afhr_quality_body_fraction", 0.65)),
        )

    @staticmethod
    def _quality_transition(
        bar: _AuctionBar,
        direction: str,
        assessment: QualityAssessment,
        reason: str,
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=f"AFHR-QUALITY-{bar.end_ts_ns}",
            event_type="AFHR_QUALITY_TRANSITION",
            previous_state="IDLE",
            next_state="RESET",
            reason_code=reason,
            reference_price=bar.close,
            details={
                "direction": direction,
                "baseline_precondition": "BASELINE_HTF_ACCEPTANCE",
                **assessment.details(),
            },
        )

    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        direction = self._baseline_acceptance_direction(bar)
        if direction is None:
            return ()

        assessment = self._assess_quality(bar)
        self._last_quality_assessment = assessment
        if assessment.enabled and not assessment.ready:
            return (
                self._quality_transition(
                    bar,
                    direction,
                    assessment,
                    "ADAPTIVE_HTF_QUALITY_WARMUP",
                ),
            )
        if assessment.enabled and not assessment.passed:
            return (
                self._quality_transition(
                    bar,
                    direction,
                    assessment,
                    "HTF_ACCEPTANCE_NOT_EXCEPTIONAL_TO_PRIOR_DISTRIBUTION",
                ),
            )

        previous_context = self._bias.context_id if self._bias is not None else None
        transitions = super()._evaluate_completed_bias(bar, snapshot)
        bias = self._bias
        if bias is not None and bias.context_id != previous_context:
            self._freshness_by_context = {
                bias.context_id: DirectionalFreshnessClock(
                    direction=bias.direction,
                    last_close_extreme=bias.close,
                    last_refresh_index=bias.created_index,
                ),
            }
            self._quality_by_context = {
                bias.context_id: {
                    "baseline_direction": direction,
                    **assessment.details(),
                },
            }
        return transitions

    def _freshness_limit_bars(self) -> int:
        periods = float(self.params.get("afhr_stale_periods", 6.0))
        if periods <= 0.0:
            raise ValueError("afhr_stale_periods must be positive")
        return max(1, int(round(self._bias_period * periods)))

    def _clear_context(self, context_id: str | None) -> None:
        if context_id is None:
            return
        self._freshness_by_context.pop(context_id, None)
        self._quality_by_context.pop(context_id, None)

    def _stale_reset(
        self,
        snapshot: PrimitiveSnapshot,
        clock: DirectionalFreshnessClock,
        limit: int,
    ) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        bias = self._bias
        assert bias is not None
        details = {
            "last_close_extreme": clock.last_close_extreme,
            "last_refresh_index": clock.last_refresh_index,
            "extreme_age_bars": clock.age(snapshot.index),
            "maximum_age_bars": limit,
            "stale_periods": self.params.get("afhr_stale_periods", 6.0),
        }
        if self._sweep is not None:
            transitions.append(
                self._sweep_transition(
                    self._sweep,
                    self._sweep.state,
                    "RESET",
                    "HTF_ACCEPTANCE_EXTREME_NOT_REFRESHED",
                    snapshot.observation.close,
                    details,
                ),
            )
            self._sweep = None
        transitions.append(
            self._bias_transition(
                bias,
                "BIAS_ACTIVE",
                "RESET",
                "HTF_ACCEPTANCE_EXTREME_NOT_REFRESHED",
                snapshot.observation.close,
                details,
            ),
        )
        context_id = bias.context_id
        self._bias = None
        self._clear_context(context_id)
        self._cooldown_until = snapshot.index + int(self.params.get("hsc_cooldown_bars", 2))
        return ScenarioStep(transitions=tuple(transitions))

    def _advance_bias(self, snapshot: PrimitiveSnapshot) -> ScenarioStep:
        bias_before = self._bias
        context_before = bias_before.context_id if bias_before is not None else None
        step = super()._advance_bias(snapshot)
        bias = self._bias
        if bias is None:
            self._clear_context(context_before)
            return step
        if not self._freshness_enabled():
            return step

        clock = self._freshness_by_context.get(bias.context_id)
        if clock is None:
            clock = DirectionalFreshnessClock(
                direction=bias.direction,
                last_close_extreme=bias.close,
                last_refresh_index=bias.created_index,
            )
            self._freshness_by_context[bias.context_id] = clock
        clock.observe(close=snapshot.observation.close, index=snapshot.index)
        limit = self._freshness_limit_bars()
        if clock.is_stale(index=snapshot.index, maximum_age_bars=limit):
            stale = self._stale_reset(snapshot, clock, limit)
            return ScenarioStep(transitions=tuple((*step.transitions, *stale.transitions)))
        return step

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        context_id = self._bias.context_id if self._bias is not None else None
        step = super().abort_active(snapshot, reason)
        self._clear_context(context_id)
        return step

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        context_id = bias.context_id
        clock = self._freshness_by_context.get(context_id)
        quality = self._quality_by_context.get(context_id, {})
        freshness_details = {
            "enabled": self._freshness_enabled(),
            "last_close_extreme": None if clock is None else clock.last_close_extreme,
            "last_refresh_index": None if clock is None else clock.last_refresh_index,
            "extreme_age_bars": None if clock is None else clock.age(snapshot.index),
            "maximum_age_bars": self._freshness_limit_bars(),
        }
        step = super()._emit(snapshot, bias, sweep)
        if step.signal is None:
            return step
        details = {
            **dict(step.signal.details),
            "adaptive_quality_contract": quality,
            "directional_freshness_contract": freshness_details,
            "ablation_contract": {
                "adaptive_quality": self._quality_enabled(),
                "extreme_freshness": self._freshness_enabled(),
            },
        }
        signal: ScenarioSignal = replace(step.signal, family="AFHR", details=details)
        return ScenarioStep(transitions=step.transitions, signal=signal)
