#!/usr/bin/env python3
"""Harvest complete boundary-auction episodes without a fixed arming horizon.

The existing v6 generator already supplies semantic liquidity boundaries, causal
structural invalidation, first-return entries and opposing-liquidity route geometry.
This module changes the decision unit from a short fixed bar window to the life of the
causal opportunity.  States are emitted when the auction materially changes: a new
extreme, a change in acceptance/retracement phase, or a sparse event-time milestone.
Future bars are used only to label immutable entry/stop/target plans.
"""
from __future__ import annotations

import math
from typing import Any, Iterator

import numpy as np
import pandas as pd

import sequential_commitment_harvest_rich as rich

policy = rich.policy
core = rich.core
_BASE_METRICS = policy._arm_metrics
EPS = policy.EPS


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _phase(progress_r: float, retrace_fraction: float, outside_ratio: float) -> str:
    if progress_r <= 0.0:
        return "FAILED_REENTRY"
    if retrace_fraction >= 0.72:
        return "DEEP_RETEST"
    if retrace_fraction >= 0.20:
        return "FIRST_RETEST_FORMING"
    if outside_ratio >= 0.60:
        return "ACCEPTED_EXPANSION"
    return "EARLY_RESPONSE"


def episode_arm_metrics(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
) -> dict[str, Any]:
    values = dict(_BASE_METRICS(data, candidate, arm, entry, stop))
    setup = candidate.setup
    departure = int(candidate.departure_index)
    side = str(setup.side)
    sign = 1.0 if side == "LONG" else -1.0
    risk = max(abs(float(entry) - float(stop)), EPS)
    atr = max(core._atr_price(data, departure), EPS)
    segment = data.iloc[departure : arm + 1]
    close = segment.close.to_numpy(float)
    directional = sign * (close - float(entry))
    running = np.maximum.accumulate(directional)
    best = max(float(running.max()), EPS)
    progress = float(directional[-1])
    retrace = max(0.0, best - progress) / best
    boundary = float(setup.upper if side == "LONG" else setup.lower)
    outside = segment.close.astype(float) > boundary if side == "LONG" else segment.close.astype(float) < boundary
    outside_ratio = float(outside.mean()) if len(outside) else 0.0
    quote = pd.to_numeric(
        segment.get("quote_volume", pd.Series(0.0, index=segment.index)),
        errors="coerce",
    ).fillna(0.0)
    outside_volume_ratio = float(quote[outside].sum()) / max(float(quote.sum()), EPS)
    movement = float(np.abs(np.diff(close)).sum()) if len(close) > 1 else 0.0
    efficiency = progress / max(movement, EPS)
    flow = _finite(values.get("arm_flow_share_signed"))
    activity = max(0.0, _finite(values.get("arm_activity_ratio")))
    effort_result = (progress / atr) / max(0.10, activity * (abs(flow) + 0.10))
    phase = _phase(progress / risk, retrace, outside_ratio)
    route_rr = max(_finite(values.get("route_rr"), 0.0), 0.0)
    values.update(
        {
            "auction_phase": phase,
            "auction_progress_r": progress / risk,
            "auction_progress_atr": progress / atr,
            "auction_best_progress_r": best / risk,
            "auction_retrace_fraction": retrace,
            "auction_outside_close_ratio": outside_ratio,
            "auction_outside_volume_ratio": outside_volume_ratio,
            "auction_path_efficiency": efficiency,
            "auction_effort_result": effort_result,
            "auction_acceptance_strength": max(0.0, progress / atr)
            * max(0.0, efficiency)
            * (0.5 * outside_ratio + 0.5 * outside_volume_ratio),
            "auction_failure_pressure": max(0.0, -progress / risk)
            + max(0.0, retrace - 0.50),
            "auction_route_headroom_r": route_rr,
        }
    )
    return values


def episode_arm_positions(
    data: pd.DataFrame,
    candidate: Any,
    source: Any,
    entry: float,
    stop: float,
    target: float,
    tick: float,
) -> Iterator[int]:
    """Emit event-time states until the first-return opportunity actually ends."""
    departure = int(candidate.departure_index)
    expiry = min(len(data) - 1, core._pending_expiry(candidate, source))
    side = str(candidate.setup.side)
    sign = 1.0 if side == "LONG" else -1.0
    risk = max(abs(float(entry) - float(stop)), EPS)
    best = sign * (float(data.iloc[departure].close) - float(entry))
    last_phase: str | None = None
    # Sparse event-time checkpoints; unlike a forced exit these do not terminate a trade.
    milestones = {1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377}
    for arm in range(departure + 1, expiry + 1):
        if not policy._pre_arm_alive(data, candidate, arm, entry, stop, target, tick):
            break
        value = sign * (float(data.iloc[arm].close) - float(entry))
        new_extreme = value > best + tick
        if new_extreme:
            best = value
        progress_r = value / risk
        retrace = max(0.0, best - value) / max(abs(best), tick)
        segment = data.iloc[departure : arm + 1]
        boundary = float(candidate.setup.upper if side == "LONG" else candidate.setup.lower)
        outside = segment.close.astype(float) > boundary if side == "LONG" else segment.close.astype(float) < boundary
        phase = _phase(progress_r, retrace, float(outside.mean()))
        changed = phase != last_phase
        if new_extreme or changed or (arm - departure) in milestones:
            yield arm
            last_phase = phase


policy._arm_metrics = episode_arm_metrics
policy._arm_positions = episode_arm_positions

if __name__ == "__main__":
    core.main()
