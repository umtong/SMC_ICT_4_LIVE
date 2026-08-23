"""Causal diagonal and channel liquidity boundaries.

EasyChart uses trend lines and channels as moving structure, while fake-outs and traps
are liquidity events at those public boundaries.  A horizontal-only ledger therefore
misses a large part of the actual decision language.  This module turns confirmed wick
swings into robust, *online* diagonal boundaries.  A line becomes available only after
its anchors are confirmed, remains active only until a later anchor changes the model,
and contributes at most one first penetration during that lifetime.

The fitted line never decides long or short by itself.  It is another public liquidity
boundary presented to the same mutually-exclusive failed/accepted auction state machine
used for horizontal liquidity.  Unpenetrated active lines may also be the nearest route
obstacle for an already valid episode.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Iterable, Sequence
import hashlib
import math

import numpy as np
import pandas as pd

import hierarchical_liquidity_bpr as hl
from semantic_liquidity_full import PoolMeta

EPS = 1e-12
LINE_TIMEFRAMES = (15, 60, 240)
MAX_ANCHORS = 5
MIN_ANCHORS = 3
SOURCE_DRIFT_HORIZON_MINUTES = 24


@dataclass(frozen=True, slots=True)
class DynamicBoundary:
    boundary_id: str
    symbol: str
    side: str
    timeframe_minutes: int
    observed_index: int
    active_until_index: int
    first_anchor_index: int
    last_anchor_index: int
    slope_per_minute: float
    intercept: float
    residual_price: float
    normalized_slope: float
    quality: float
    anchor_count: int
    historical_span_minutes: int
    channel_quality: float
    channel_width_at_observation: float
    first_penetration_index: int | None

    def value_at(self, index: int) -> float:
        return self.intercept + self.slope_per_minute * float(index)

    @property
    def is_channel_edge(self) -> bool:
        return self.channel_quality > 0.0


_BOUNDARIES: dict[str, list[DynamicBoundary]] = {}
_SOURCE_BOUNDARY: dict[str, DynamicBoundary] = {}


def clear_symbol(symbol: str) -> None:
    _BOUNDARIES.pop(symbol, None)
    stale = [key for key, value in _SOURCE_BOUNDARY.items() if value.symbol == symbol]
    for key in stale:
        _SOURCE_BOUNDARY.pop(key, None)


def boundaries_for(symbol: str) -> tuple[DynamicBoundary, ...]:
    return tuple(_BOUNDARIES.get(symbol, ()))


def boundary_for_source(level_id: str) -> DynamicBoundary | None:
    return _SOURCE_BOUNDARY.get(level_id)


def _finite(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _atr_price(data: pd.DataFrame, index: int, window: int = 120) -> float:
    frame = data.iloc[max(0, index - window):index]
    if frame.empty:
        return 0.0
    prior_close = frame.close.shift(1)
    tr = pd.concat(
        [
            frame.high - frame.low,
            (frame.high - prior_close).abs(),
            (frame.low - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return max(0.0, _finite(tr.median(), 0.0))


def _event_index(data: pd.DataFrame, event_time_ns: int) -> int:
    timestamp = pd.Timestamp(event_time_ns, unit="ns", tz="UTC")
    return min(
        len(data) - 1,
        max(0, int(data.index.searchsorted(timestamp, side="left"))),
    )


def _anchor_rows(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    *,
    side: str,
    timeframe: int,
) -> list[tuple[int, int, float, str]]:
    dedup: dict[tuple[int, int], tuple[int, int, float, str]] = {}
    for level in levels:
        if level.side != side or int(level.timeframe_minutes) != timeframe:
            continue
        kind = str(level.source_kind)
        if "CONFIRMED_EXTERNAL" not in kind:
            continue
        event_index = _event_index(data, int(level.event_time_ns))
        observed = int(level.observed_index_1m)
        quantum = max(abs(level.upper - level.lower), abs(level.price) * 1e-8, EPS)
        key = (event_index, int(round(float(level.price) / quantum)))
        candidate = (event_index, observed, float(level.price), level.level_id)
        previous = dedup.get(key)
        if previous is None or candidate[1] < previous[1]:
            dedup[key] = candidate
    return sorted(dedup.values(), key=lambda item: (item[1], item[0], item[2], item[3]))


def _fit(points: Sequence[tuple[int, int, float, str]], data: pd.DataFrame) -> DynamicBoundary | None:
    if len(points) < MIN_ANCHORS:
        return None
    x = [int(item[0]) for item in points]
    y = [float(item[2]) for item in points]
    if x[-1] <= x[0]:
        return None
    pairwise: list[float] = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            elapsed = x[j] - x[i]
            if elapsed > 0:
                pairwise.append((y[j] - y[i]) / elapsed)
    if not pairwise:
        return None
    slope = float(median(pairwise))
    intercept = float(median(price - slope * index for index, price in zip(x, y, strict=True)))
    residuals = [abs(price - (intercept + slope * index)) for index, price in zip(x, y, strict=True)]
    residual = float(median(residuals)) if residuals else 0.0
    observed = max(int(item[1]) for item in points)
    atr = max(_atr_price(data, observed), EPS)
    normalized_slope = slope * 60.0 / atr
    span_minutes = x[-1] - x[0]
    spacing_quality = min(
        1.0,
        span_minutes / max(240.0, 2.0 * (points[-1][0] - points[-2][0])),
    )
    residual_quality = math.exp(-residual / max(0.20 * atr, EPS))
    anchor_quality = min(1.0, len(points) / 4.0)
    slope_quality = math.exp(-max(0.0, abs(normalized_slope) - 2.5))
    quality = float(
        np.clip(
            spacing_quality * residual_quality * anchor_quality * slope_quality,
            0.0,
            1.0,
        )
    )
    if quality < 0.18:
        return None
    identity = hashlib.sha1(
        "|".join(item[3] for item in points).encode("utf-8")
    ).hexdigest()[:16]
    return DynamicBoundary(
        boundary_id=f"DYNAMIC:{identity}",
        symbol="",
        side="",
        timeframe_minutes=0,
        observed_index=observed,
        active_until_index=len(data) - 1,
        first_anchor_index=x[0],
        last_anchor_index=x[-1],
        slope_per_minute=slope,
        intercept=intercept,
        residual_price=residual,
        normalized_slope=normalized_slope,
        quality=quality,
        anchor_count=len(points),
        historical_span_minutes=span_minutes,
        channel_quality=0.0,
        channel_width_at_observation=0.0,
        first_penetration_index=None,
    )


def _raw_models(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
) -> list[DynamicBoundary]:
    output: list[DynamicBoundary] = []
    for timeframe in LINE_TIMEFRAMES:
        for side in ("HIGH", "LOW"):
            anchors = _anchor_rows(data, levels, side=side, timeframe=timeframe)
            models: list[DynamicBoundary] = []
            for position in range(MIN_ANCHORS - 1, len(anchors)):
                points = anchors[max(0, position - MAX_ANCHORS + 1):position + 1]
                fitted = _fit(points, data)
                if fitted is None:
                    continue
                active_until = (
                    int(anchors[position + 1][1])
                    if position + 1 < len(anchors)
                    else len(data) - 1
                )
                if active_until <= fitted.observed_index + 1:
                    continue
                fitted = replace(
                    fitted,
                    boundary_id=(
                        f"{fitted.boundary_id}:{symbol}:{timeframe}:{side}:{position}"
                    ),
                    symbol=symbol,
                    side=side,
                    timeframe_minutes=timeframe,
                    active_until_index=active_until,
                )
                models.append(fitted)
            output.extend(models)
    return output


def _attach_channels(
    models: Sequence[DynamicBoundary],
    data: pd.DataFrame,
) -> list[DynamicBoundary]:
    """Attach only information observable when both fitted edges first coexist.

    ``active_until_index`` reconstructs when a historical online line was replaced by a
    later confirmed anchor.  It may be used to decide whether both lines coexisted at an
    observation, but never to reward how long they survive in the future.  The earlier
    implementation used future overlap duration in channel quality; that leaked the
    next anchor time into decision features.  Quality now depends only on anchor history,
    residuals, width and slope agreement already known at the joint observation.
    """
    output: list[DynamicBoundary] = []
    for model in models:
        candidates = [
            other
            for other in models
            if other.symbol == model.symbol
            and other.timeframe_minutes == model.timeframe_minutes
            and other.side != model.side
            and other.observed_index <= model.active_until_index
            and other.active_until_index >= model.observed_index
        ]
        best_quality = 0.0
        best_width = 0.0
        for other in candidates:
            observation = max(model.observed_index, other.observed_index)
            # Both models must actually have been active at the joint observation.
            if observation > model.active_until_index or observation > other.active_until_index:
                continue
            width = abs(model.value_at(observation) - other.value_at(observation))
            atr = max(_atr_price(data, observation), EPS)
            if width < 0.50 * atr:
                continue
            horizon = max(60, 4 * model.timeframe_minutes)
            slope_gap = abs(model.slope_per_minute - other.slope_per_minute) * horizon
            parallel = math.exp(-slope_gap / max(width, atr))
            history_quality = min(
                1.0,
                min(model.historical_span_minutes, other.historical_span_minutes)
                / max(240.0, 4.0 * model.timeframe_minutes),
            )
            width_quality = 1.0 - math.exp(-width / max(atr, EPS))
            channel_quality = (
                min(model.quality, other.quality)
                * parallel
                * history_quality
                * width_quality
            )
            if channel_quality > best_quality:
                best_quality, best_width = channel_quality, width
        output.append(
            replace(
                model,
                channel_quality=float(np.clip(best_quality, 0.0, 1.0)),
                channel_width_at_observation=float(best_width),
            )
        )
    return output


def _first_penetration(
    model: DynamicBoundary,
    data: pd.DataFrame,
    tick: float,
) -> int | None:
    start = max(model.observed_index + 1, model.last_anchor_index + 1)
    end = min(len(data) - 1, model.active_until_index)
    if start > end:
        return None
    atr = max(_atr_price(data, model.observed_index), tick)
    drift = abs(model.slope_per_minute) * SOURCE_DRIFT_HORIZON_MINUTES
    tolerance = max(2.0 * tick, 1.5 * model.residual_price, 0.04 * atr, drift)
    for index in range(start, end + 1):
        boundary = model.value_at(index)
        previous_close = float(data.iloc[index - 1].close)
        row = data.iloc[index]
        if model.side == "HIGH":
            crossed = previous_close < boundary and float(row.high) > boundary + tolerance
        else:
            crossed = previous_close > boundary and float(row.low) < boundary - tolerance
        if crossed:
            return index
    return None


def build_dynamic_boundaries(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    tick: float,
) -> list[DynamicBoundary]:
    clear_symbol(symbol)
    models = _attach_channels(_raw_models(symbol, data, levels), data)
    output = [
        replace(
            model,
            first_penetration_index=_first_penetration(model, data, tick),
        )
        for model in models
    ]
    _BOUNDARIES[symbol] = output
    return output


def source_levels(
    symbol: str,
    data: pd.DataFrame,
    models: Iterable[DynamicBoundary],
    tick: float,
    static_levels: Sequence[hl.LiquidityLevel],
) -> tuple[list[hl.LiquidityLevel], dict[str, PoolMeta]]:
    levels: list[hl.LiquidityLevel] = []
    metadata: dict[str, PoolMeta] = {}
    for model in models:
        interaction = model.first_penetration_index
        if interaction is None:
            continue
        price = float(model.value_at(interaction))
        atr = max(_atr_price(data, interaction), tick)
        drift = abs(model.slope_per_minute) * SOURCE_DRIFT_HORIZON_MINUTES
        width = max(2.0 * tick, 1.5 * model.residual_price, 0.04 * atr, drift)
        coincident = 0.0
        for level in static_levels:
            if int(level.observed_index_1m) >= interaction:
                continue
            if abs(float(level.price) - price) <= max(
                width,
                abs(level.upper - level.lower),
            ):
                coincident = max(
                    coincident,
                    float(level.timeframe_minutes) / 240.0,
                )
        source_id = f"{model.boundary_id}:PENETRATION:{interaction}"
        kind = (
            f"{model.timeframe_minutes}M_DYNAMIC_CHANNEL_{model.side}"
            if model.is_channel_edge
            else f"{model.timeframe_minutes}M_DYNAMIC_TRENDLINE_{model.side}"
        )
        strength = model.quality * (
            1.0 + 0.50 * model.channel_quality + 0.25 * coincident
        )
        level = hl.LiquidityLevel(
            level_id=source_id,
            symbol=symbol,
            side=model.side,
            timeframe_minutes=model.timeframe_minutes,
            span=model.anchor_count,
            price=price,
            lower=price - width,
            upper=price + width,
            event_time_ns=int(data.index[model.last_anchor_index].value),
            observed_time_ns=int(data.index[model.observed_index].value),
            observed_index_1m=model.observed_index,
            strength_ratio=float(strength),
            defense_count=model.anchor_count,
            source_kind=kind,
            first_penetration_index=int(interaction),
        )
        levels.append(level)
        metadata[source_id] = PoolMeta(
            pool_kind=(
                "DYNAMIC_DIAGONAL_CHANNEL_EDGE"
                if model.is_channel_edge
                else "DYNAMIC_DIAGONAL_TRENDLINE"
            ),
            member_count=model.anchor_count,
            member_timeframes=str(model.timeframe_minutes),
            accumulated=model.anchor_count >= 4,
            direction_source=True,
            route_obstacle=False,
            semantic_weight=float(strength),
        )
        _SOURCE_BOUNDARY[source_id] = model
    return levels, metadata


def active_route_boundaries(
    symbol: str,
    data: pd.DataFrame,
    index: int,
    entry: float,
    side: str,
    tick: float,
) -> list[tuple[DynamicBoundary, float]]:
    wanted = "HIGH" if side == "LONG" else "LOW"
    output: list[tuple[DynamicBoundary, float]] = []
    for model in _BOUNDARIES.get(symbol, ()):
        if model.side != wanted:
            continue
        if not (model.observed_index < index <= model.active_until_index):
            continue
        if (
            model.first_penetration_index is not None
            and model.first_penetration_index <= index
        ):
            continue
        price = float(model.value_at(index))
        if side == "LONG" and price <= entry + tick:
            continue
        if side == "SHORT" and price >= entry - tick:
            continue
        output.append((model, price))
    output.sort(
        key=lambda item: (
            abs(item[1] - entry),
            -item[0].channel_quality,
            -item[0].quality,
            -item[0].timeframe_minutes,
            item[0].boundary_id,
        )
    )
    return output


__all__ = [
    "DynamicBoundary",
    "active_route_boundaries",
    "boundaries_for",
    "boundary_for_source",
    "build_dynamic_boundaries",
    "source_levels",
]
