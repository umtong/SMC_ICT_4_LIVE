"""Directional-window sequential likelihood state for Candidate 05 v35b."""
from __future__ import annotations

from dataclasses import dataclass
import math

from sequential_flow_regime_logic import FAILURE_LOG_LIKELIHOOD
from sequential_flow_regime_logic import SUCCESS_LOG_LIKELIHOOD
from sequential_flow_regime_logic import UPPER_LOG_LIKELIHOOD_BOUNDARY


@dataclass(frozen=True, slots=True)
class DirectionalEvidenceWindow:
    log_likelihood: float = 0.0
    observations: int = 0
    first_index: int = -1
    last_index: int = -1
    range_high: float = -math.inf
    range_low: float = math.inf

    @property
    def available(self) -> bool:
        return (
            self.observations > 0
            and self.first_index >= 0
            and math.isfinite(self.range_high)
            and math.isfinite(self.range_low)
            and self.range_high > self.range_low
        )


@dataclass(frozen=True, slots=True)
class DirectionalSequentialFlowState:
    upward: DirectionalEvidenceWindow = DirectionalEvidenceWindow()
    downward: DirectionalEvidenceWindow = DirectionalEvidenceWindow()
    total_informative_observations: int = 0
    last_index: int = -1

    def reference(self, side: int) -> DirectionalEvidenceWindow:
        if side == 1:
            return self.upward
        if side == -1:
            return self.downward
        raise ValueError("side must be -1 or 1")


@dataclass(frozen=True, slots=True)
class DirectionalSequentialFlowUpdate:
    state: DirectionalSequentialFlowState
    decision: int
    informative: bool
    upward_restarted: bool
    downward_restarted: bool


def _advance_window(
    *,
    window: DirectionalEvidenceWindow,
    success: bool,
    high: float,
    low: float,
    bar_index: int,
) -> tuple[DirectionalEvidenceWindow, bool]:
    increment = SUCCESS_LOG_LIKELIHOOD if success else FAILURE_LOG_LIKELIHOOD
    likelihood = max(0.0, window.log_likelihood + increment)
    if likelihood <= 0.0:
        return DirectionalEvidenceWindow(), window.observations > 0
    if window.observations == 0:
        return (
            DirectionalEvidenceWindow(
                log_likelihood=likelihood,
                observations=1,
                first_index=bar_index,
                last_index=bar_index,
                range_high=high,
                range_low=low,
            ),
            False,
        )
    return (
        DirectionalEvidenceWindow(
            log_likelihood=likelihood,
            observations=window.observations + 1,
            first_index=window.first_index,
            last_index=bar_index,
            range_high=max(window.range_high, high),
            range_low=min(window.range_low, low),
        ),
        False,
    )


def update_directional_sequential_flow(
    *,
    state: DirectionalSequentialFlowState,
    flow_60s: float,
    high: float,
    low: float,
    bar_index: int,
    minimum_absolute_flow: float,
) -> DirectionalSequentialFlowUpdate:
    """Update direction-specific LLRs and their matching price evidence windows."""
    values = (flow_60s, high, low, minimum_absolute_flow)
    if not all(math.isfinite(float(value)) for value in values):
        return DirectionalSequentialFlowUpdate(state, 0, False, False, False)
    if minimum_absolute_flow < 0.0 or high < low:
        raise ValueError("invalid directional sequential observation")
    if abs(flow_60s) < minimum_absolute_flow:
        return DirectionalSequentialFlowUpdate(state, 0, False, False, False)

    upward, up_restarted = _advance_window(
        window=state.upward,
        success=flow_60s > 0.0,
        high=high,
        low=low,
        bar_index=bar_index,
    )
    downward, down_restarted = _advance_window(
        window=state.downward,
        success=flow_60s < 0.0,
        high=high,
        low=low,
        bar_index=bar_index,
    )
    updated = DirectionalSequentialFlowState(
        upward=upward,
        downward=downward,
        total_informative_observations=state.total_informative_observations + 1,
        last_index=bar_index,
    )
    decision = (
        1
        if upward.log_likelihood >= UPPER_LOG_LIKELIHOOD_BOUNDARY
        else -1
        if downward.log_likelihood >= UPPER_LOG_LIKELIHOOD_BOUNDARY
        else 0
    )
    return DirectionalSequentialFlowUpdate(
        state=updated,
        decision=decision,
        informative=True,
        upward_restarted=up_restarted,
        downward_restarted=down_restarted,
    )


__all__ = [
    "DirectionalEvidenceWindow",
    "DirectionalSequentialFlowState",
    "DirectionalSequentialFlowUpdate",
    "update_directional_sequential_flow",
]
