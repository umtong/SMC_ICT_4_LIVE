#!/usr/bin/env python3
"""Candidate 05 v35b: directional evidence windows follow LLR restarts."""
from __future__ import annotations

import math

from directional_sequential_flow_logic import DirectionalSequentialFlowState
from directional_sequential_flow_logic import update_directional_sequential_flow
from sequential_flow_regime_logic import SequentialFlowState
from sequential_flow_regime_logic import sequential_release_breakout
from strategy_v35_sequential_flow_regime import SequentialFlowRegimeStrategy


class DirectionalSequentialFlowRegimeStrategy(SequentialFlowRegimeStrategy):
    """Repair v35's mismatch between restarted LLRs and an unrestarted range.

    Statistical probabilities, likelihood boundary, informative-flow threshold,
    release predicates, first-retest rule, stop, target, fees, slippage, 3% NAV
    sizing and Nautilus execution remain unchanged. Only the price range used by
    each directional decision now starts and resets with that direction's LLR.
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        self.sequential_state = DirectionalSequentialFlowState()
        self.diagnostics.update(
            {
                "directional_sequential_upward_restarts": 0,
                "directional_sequential_downward_restarts": 0,
                "directional_sequential_reference_missing": 0,
            },
        )

    def _reset_sequential_state(self) -> None:
        if self.sequential_state.total_informative_observations:
            self.diagnostics["sequential_flow_state_resets"] += 1
        self.sequential_state = DirectionalSequentialFlowState()

    def _advance_sequential_detector(self, row: dict[str, float | int]) -> None:
        prior = self.sequential_state
        active_starts = [
            window.first_index
            for window in (prior.upward, prior.downward)
            if window.observations > 0 and window.first_index >= 0
        ]
        if active_starts and self.bar_index - min(active_starts) > self.config.max_hold_bars:
            self._reset_sequential_state()
            prior = self.sequential_state

        update = update_directional_sequential_flow(
            state=prior,
            flow_60s=self._feature("flow_60s"),
            high=float(row["high"]),
            low=float(row["low"]),
            bar_index=self.bar_index,
            minimum_absolute_flow=self.config.acceptance_flow_min,
        )
        self.sequential_state = update.state
        if update.informative:
            self.diagnostics["sequential_flow_informative_minutes"] += 1
        if update.upward_restarted:
            self.diagnostics["directional_sequential_upward_restarts"] += 1
        if update.downward_restarted:
            self.diagnostics["directional_sequential_downward_restarts"] += 1

        side = update.decision
        if side == 0:
            return
        self.diagnostics["sequential_flow_likelihood_decisions"] += 1
        reference = prior.reference(side)
        if not reference.available:
            self.diagnostics["directional_sequential_reference_missing"] += 1
            self.diagnostics["sequential_flow_release_rejections"] += 1
            return

        atr = self._atr()
        if not sequential_release_breakout(
            side=side,
            prior_high=reference.range_high,
            prior_low=reference.range_low,
            open_price=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            notional_burst=self._feature("notional_burst"),
            bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
            minimum_break_distance_atr=self.config.acceptance_close_atr,
            minimum_flow=self.config.acceptance_flow_min,
            minimum_efficiency=self.config.acceptance_efficiency_min,
            minimum_notional_burst=self.config.sweep_min_notional_burst,
            minimum_depth_withdrawal=self.config.acceptance_depth_withdrawal_min,
            minimum_close_location=self.config.acceptance_close_location,
        ):
            self.diagnostics["sequential_flow_release_rejections"] += 1
            return

        self.diagnostics["sequential_flow_release_confirmations"] += 1
        legacy_reference = SequentialFlowState(
            upward_log_likelihood=update.state.upward.log_likelihood,
            downward_log_likelihood=update.state.downward.log_likelihood,
            informative_observations=reference.observations,
            first_index=reference.first_index,
            last_index=reference.last_index,
            range_high=reference.range_high,
            range_low=reference.range_low,
        )
        self._arm_sequential_release(
            side=side,
            prior=legacy_reference,
            row=row,
            atr=atr,
        )
        self._reset_sequential_state()


__all__ = ["DirectionalSequentialFlowRegimeStrategy"]
