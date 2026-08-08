"""Pure causal decision for Candidate 18 v10 second-leg relaunch.

The opening auction and defended retest only arm an opportunity. Entry is
allowed after a strictly later auction leg closes beyond the opening/retest
extreme with renewed price-flow-queue agreement. The accepted range boundary
remains a pre-entry state invalidation; execution uses the opposite pre-event
range boundary as the structural stop anchor.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class RelaunchDecision:
    state: str
    reason: str
    launch_level: float


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


def evaluate_second_leg_relaunch(
    *,
    side: int,
    atr: float,
    accepted_boundary: float,
    opening_extreme: float,
    retest_high: float,
    retest_low: float,
    close: float,
    tail_flow_15s: float,
    full_flow_60s: float,
    return_60s_bps: float,
    efficiency_60s: float,
    depth_imbalance_1: float,
    buffer_atr: float,
    tail_flow_min: float,
    full_flow_min: float,
    efficiency_min: float,
    queue_min: float,
) -> RelaunchDecision:
    """Classify a post-retest bar without looking beyond its observation time."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not _finite(
        atr,
        accepted_boundary,
        opening_extreme,
        retest_high,
        retest_low,
        close,
        tail_flow_15s,
        full_flow_60s,
        return_60s_bps,
        efficiency_60s,
        depth_imbalance_1,
        buffer_atr,
        tail_flow_min,
        full_flow_min,
        efficiency_min,
        queue_min,
    ):
        return RelaunchDecision("WAIT", "RELAUNCH_INPUT_NOT_FINITE", float("nan"))
    if atr <= 0.0:
        raise ValueError("atr must be positive")
    if min(buffer_atr, tail_flow_min, full_flow_min, efficiency_min, queue_min) < 0.0:
        raise ValueError("relaunch thresholds must be non-negative")

    launch_level = (
        max(opening_extreme, retest_high) + buffer_atr * atr
        if side > 0
        else min(opening_extreme, retest_low) - buffer_atr * atr
    )

    # Before entry, a close back through the accepted boundary means that the
    # first auction is no longer accepted. This is a state failure, not a stop.
    if (side > 0 and close <= accepted_boundary) or (
        side < 0 and close >= accepted_boundary
    ):
        return RelaunchDecision(
            "INVALIDATED",
            "ACCEPTED_RANGE_LOST_BEFORE_SECOND_LEG",
            launch_level,
        )

    if (side > 0 and close <= launch_level) or (
        side < 0 and close >= launch_level
    ):
        return RelaunchDecision(
            "WAIT",
            "SECOND_LEG_HAS_NOT_CLOSED_BEYOND_LAUNCH_LEVEL",
            launch_level,
        )
    if side * tail_flow_15s < tail_flow_min:
        return RelaunchDecision(
            "WAIT",
            "SECOND_LEG_TAIL_FLOW_NOT_DIRECTIONAL",
            launch_level,
        )
    if side * full_flow_60s < full_flow_min:
        return RelaunchDecision(
            "WAIT",
            "SECOND_LEG_FULL_FLOW_NOT_DIRECTIONAL",
            launch_level,
        )
    if side * return_60s_bps <= 0.0:
        return RelaunchDecision(
            "WAIT",
            "SECOND_LEG_RETURN_NOT_DIRECTIONAL",
            launch_level,
        )
    if efficiency_60s < efficiency_min:
        return RelaunchDecision(
            "WAIT",
            "SECOND_LEG_PROGRESS_NOT_EFFICIENT",
            launch_level,
        )
    if side * depth_imbalance_1 <= queue_min:
        return RelaunchDecision(
            "WAIT",
            "SECOND_LEG_DISPLAYED_QUEUE_NOT_SUPPORTIVE",
            launch_level,
        )

    return RelaunchDecision(
        "CONFIRMED",
        "STRICTLY_LATER_SECOND_LEG_RELAUNCH_CONFIRMED",
        launch_level,
    )


__all__ = ["RelaunchDecision", "evaluate_second_leg_relaunch"]
