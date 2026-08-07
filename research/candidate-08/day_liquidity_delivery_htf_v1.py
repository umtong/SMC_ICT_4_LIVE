"""Higher-timeframe draw and external-liquidity destination for day-delivery V1."""

from __future__ import annotations

from math import isfinite
from typing import Iterable

import numpy as np

from day_liquidity_delivery_context_v1 import (
    DayLiquidityDeliveryConfig,
    DrawContext,
    Swing,
    aggregate_four_hour_bars,
    is_swing_high,
    is_swing_low,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource


def build_draw_contexts(
    bars: tuple[FiveMinuteBar, ...],
    config: DayLiquidityDeliveryConfig,
) -> tuple[DrawContext | None, ...]:
    """Map each completed M5 bar to the latest completed H4 structural draw."""

    h4 = aggregate_four_hour_bars(bars, config)
    result: list[DrawContext | None] = [None] * len(bars)
    if not h4:
        return tuple(result)

    highs, lows = [b.high for b in h4], [b.low for b in h4]
    confirmed_highs: list[Swing] = []
    confirmed_lows: list[Swing] = []
    broken: set[str] = set()
    active: DrawContext | None = None
    after_h4: list[DrawContext | None] = []

    for position, current in enumerate(h4):
        center = position - config.h4_swing_span
        if center >= config.h4_swing_span:
            candidate = h4[center]
            if is_swing_high(highs, center, config.h4_swing_span):
                confirmed_highs.append(
                    Swing(
                        "H4_HIGH", candidate.high, candidate.index, candidate.end_time_ns,
                        current.index, current.end_time_ns,
                    )
                )
            if is_swing_low(lows, center, config.h4_swing_span):
                confirmed_lows.append(
                    Swing(
                        "H4_LOW", candidate.low, candidate.index, candidate.end_time_ns,
                        current.index, current.end_time_ns,
                    )
                )

        if active is not None:
            invalid = current.close < active.origin_level if active.direction > 0 else current.close > active.origin_level
            if invalid:
                active = None

        prior_close = h4[position - 1].close if position else current.open
        ready = all(
            isfinite(value)
            for value in (current.atr, current.prior_body_median, current.prior_range_median)
        )
        displaced = ready and current.body >= current.prior_body_median and current.range >= current.prior_range_median
        if displaced and confirmed_highs and confirmed_lows:
            high, low = confirmed_highs[-1], confirmed_lows[-1]
            bull = (
                high.swing_id not in broken
                and prior_close <= high.price < current.close
                and current.close > current.open
                and current.close_location >= config.h4_close_location
                and low.price < current.close
            )
            bear = (
                low.swing_id not in broken
                and prior_close >= low.price > current.close
                and current.close < current.open
                and current.close_location <= 1.0 - config.h4_close_location
                and high.price > current.close
            )
            if bull:
                broken.add(high.swing_id)
                active = DrawContext(
                    1, high.swing_id, high.price, low.swing_id, low.price,
                    current.end_time_ns, current.index, current.atr,
                )
            elif bear:
                broken.add(low.swing_id)
                active = DrawContext(
                    -1, low.swing_id, low.price, high.swing_id, high.price,
                    current.end_time_ns, current.index, current.atr,
                )
        after_h4.append(active)

    endings = np.asarray([b.last_five_index for b in h4], dtype=np.int64)
    for five_index in range(len(bars)):
        position = int(np.searchsorted(endings, five_index, side="right") - 1)
        if position >= 0:
            result[five_index] = after_h4[position]
    return tuple(result)


def levels_after_five_bar(
    snapshots: tuple[tuple[ExternalLevel, ...], ...], five_index: int
) -> tuple[ExternalLevel, ...]:
    if not snapshots:
        return ()
    return snapshots[min(max(int(five_index) + 1, 0), len(snapshots) - 1)]


def select_htf_target(
    levels: Iterable[ExternalLevel],
    *,
    direction: int,
    reference: float,
    h4_atr: float,
    config: DayLiquidityDeliveryConfig,
) -> ExternalLevel | None:
    kind = LevelKind.HIGH if direction > 0 else LevelKind.LOW
    candidates = [
        level
        for level in levels
        if level.kind is kind
        and (level.level > reference if direction > 0 else level.level < reference)
    ]
    higher = [level for level in candidates if level.source in (LevelSource.DAY, LevelSource.WEEK)]
    if higher:
        return min(higher, key=lambda level: abs(level.level - reference))
    if not isfinite(h4_atr) or h4_atr <= 0.0:
        return None
    minimum = config.h4_target_minimum_atr * h4_atr
    fallback = [
        level
        for level in candidates
        if level.source is LevelSource.FOUR_HOUR and abs(level.level - reference) >= minimum
    ]
    return min(fallback, key=lambda level: abs(level.level - reference)) if fallback else None


def target_still_active(
    snapshots: tuple[tuple[ExternalLevel, ...], ...], five_index: int, target_id: str
) -> bool:
    return any(level.level_id == target_id for level in levels_after_five_bar(snapshots, five_index))


def same_draw(left: DrawContext | None, right: DrawContext) -> bool:
    return left is not None and left.signature == right.signature


__all__ = [
    "build_draw_contexts", "levels_after_five_bar", "same_draw", "select_htf_target",
    "target_still_active",
]
