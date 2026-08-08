"""Backward-only activity features for Candidate 37."""
from __future__ import annotations

import math
from statistics import median
from typing import Sequence

from model import BarObservation, RouteConfig, Snapshot


def finite_positive(value: float) -> bool:
    return math.isfinite(value) and value > 0.0


def true_range(bar: BarObservation, previous_close: float) -> float:
    return max(
        bar.high - bar.low,
        abs(bar.high - previous_close),
        abs(bar.low - previous_close),
    )


def atr_before(bars: Sequence[BarObservation], index: int, period: int) -> float:
    if index < period + 1:
        return math.nan
    values = [
        true_range(bars[location], bars[location - 1].close)
        for location in range(index - period, index)
    ]
    result = sum(values) / len(values) if values else math.nan
    return result if finite_positive(result) else math.nan


def volume_ratio_before(
    bars: Sequence[BarObservation], index: int, lookback: int
) -> float:
    if index < max(12, lookback // 2):
        return math.nan
    history = [
        float(bar.volume)
        for bar in bars[max(0, index - lookback) : index]
        if finite_positive(float(bar.volume))
    ]
    if len(history) < 12:
        return math.nan
    baseline = float(median(history))
    current = float(bars[index].volume)
    if not finite_positive(baseline) or not math.isfinite(current) or current < 0.0:
        return math.nan
    return current / baseline


def basic_activity(
    bars: Sequence[BarObservation], index: int, config: RouteConfig
) -> tuple[float, float, float, float, int, float]:
    atr = atr_before(bars, index, config.atr_period)
    if not finite_positive(atr):
        return (math.nan,) * 4 + (0, math.nan)
    previous_close = bars[index - 1].close
    tr = true_range(bars[index], previous_close)
    net = (bars[index].close - previous_close) / atr
    tr_atr = tr / atr
    volume_ratio = volume_ratio_before(bars, index, config.activity_lookback)
    direction = 1 if net > 0.0 else -1 if net < 0.0 else 0
    efficiency = abs(bars[index].close - previous_close) / tr if tr > 0.0 else 0.0
    return atr, tr_atr, net, volume_ratio, direction, efficiency


def snapshot(
    bars: Sequence[BarObservation], index: int, config: RouteConfig
) -> Snapshot | None:
    atr, tr_atr, net_atr, volume_ratio, direction, efficiency = basic_activity(
        bars, index, config
    )
    if not all(math.isfinite(value) for value in (atr, tr_atr, net_atr, volume_ratio)):
        return None
    activity = math.sqrt(max(0.0, tr_atr) * max(0.0, volume_ratio))
    prior_activities: list[float] = []
    prior_directions: list[int] = []
    for location in range(index - config.ramp_bars, index):
        if location <= 0:
            continue
        _, prior_tr, prior_net, prior_volume, prior_direction, _ = basic_activity(
            bars, location, config
        )
        if math.isfinite(prior_tr) and math.isfinite(prior_volume):
            prior_activities.append(
                math.sqrt(max(0.0, prior_tr) * max(0.0, prior_volume))
            )
            prior_directions.append(prior_direction if prior_net != 0.0 else 0)
    abruptness = activity / max(max(prior_activities, default=0.0), 0.25)
    ramp_score = 0.0
    direction_share = 0.0
    if len(prior_activities) >= config.ramp_bars:
        steps = sum(
            current > previous
            for previous, current in zip(prior_activities, prior_activities[1:])
        )
        monotonic = steps / max(1, len(prior_activities) - 1)
        growth = (prior_activities[-1] - prior_activities[0]) / max(
            0.5, float(median(prior_activities))
        )
        ramp_score = max(
            0.0,
            0.55 * monotonic + 0.45 * min(1.5, max(0.0, growth)),
        )
        if direction:
            direction_share = sum(item == direction for item in prior_directions) / len(prior_directions)
    return Snapshot(
        atr=atr,
        tr_atr=tr_atr,
        net_atr=net_atr,
        direction=direction,
        volume_ratio=volume_ratio,
        activity=activity,
        efficiency=efficiency,
        abruptness=abruptness,
        ramp_score=ramp_score,
        ramp_direction_share=direction_share,
    )
