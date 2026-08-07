"""Shared causal types and completed-bar transforms for day-liquidity delivery V1."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from range_fvg_logic import ExternalLevel, FiveMinuteBar

RAID_FAMILY = "DRAW_ALIGNED_RAID_REVERSAL"
ACCEPTANCE_FAMILY = "DRAW_ALIGNED_ACCEPTANCE_CONTINUATION"
IMPLEMENTATION_REVISION = "DAY_LIQUIDITY_DELIVERY_ROUTER_V1"
FOUR_HOURS_NS = 4 * 60 * 60 * 1_000_000_000
FIFTEEN_MINUTES_NS = 15 * 60 * 1_000_000_000
ONE_DAY_NS = 24 * 60 * 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class DayLiquidityDeliveryConfig:
    h4_swing_span: int = 2
    h4_displacement_lookback: int = 12
    h4_close_location: float = 2.0 / 3.0
    h4_target_minimum_atr: float = 0.75
    session_boundary_excursion_atr: float = 0.05
    acceptance_close_location: float = 2.0 / 3.0
    five_swing_span: int = 2
    five_displacement_lookback: int = 12
    five_close_location: float = 2.0 / 3.0
    structural_stop_buffer_atr: float = 0.05
    maximum_delivery_minutes: int = 180

    def validate(self) -> None:
        if self.h4_swing_span != 2 or self.five_swing_span != 2:
            raise ValueError("V1 fixes both causal swing spans at two completed bars")
        if self.h4_displacement_lookback < 6 or self.five_displacement_lookback < 6:
            raise ValueError("displacement medians require at least six prior bars")
        for name, value in (
            ("h4_close_location", self.h4_close_location),
            ("acceptance_close_location", self.acceptance_close_location),
            ("five_close_location", self.five_close_location),
        ):
            if not 0.5 < value < 1.0:
                raise ValueError(f"invalid {name}")
        if min(
            self.h4_target_minimum_atr,
            self.session_boundary_excursion_atr,
            self.structural_stop_buffer_atr,
        ) <= 0.0:
            raise ValueError("relative distance contracts must be positive")
        if not 30 <= self.maximum_delivery_minutes <= 360:
            raise ValueError("day delivery must complete within the session day")


@dataclass(frozen=True, slots=True)
class Swing:
    kind: str
    price: float
    formed_index: int
    formed_time_ns: int
    confirmed_index: int
    confirmed_time_ns: int

    @property
    def swing_id(self) -> str:
        return f"{self.kind}-{self.formed_time_ns}-{self.price:.12g}"


@dataclass(frozen=True, slots=True)
class FourHourBar:
    index: int
    end_time_ns: int
    last_five_index: int
    open: float
    high: float
    low: float
    close: float
    atr: float
    prior_body_median: float
    prior_range_median: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        return (self.close - self.low) / self.range if self.range > 0.0 else 0.5


@dataclass(frozen=True, slots=True)
class DrawContext:
    direction: int
    break_swing_id: str
    break_level: float
    origin_swing_id: str
    origin_level: float
    observed_time_ns: int
    h4_index: int
    h4_atr: float

    @property
    def signature(self) -> tuple[int, str, str]:
        return self.direction, self.break_swing_id, self.origin_swing_id


@dataclass(frozen=True, slots=True)
class FifteenMinuteBar:
    index: int
    start_time_ns: int
    end_time_ns: int
    first_five_index: int
    last_five_index: int
    open: float
    high: float
    low: float
    close: float
    atr: float
    prior_body_median: float
    prior_range_median: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        return (self.close - self.low) / self.range if self.range > 0.0 else 0.5


@dataclass(frozen=True, slots=True)
class SessionRange:
    day_start_ns: int
    name: str
    high: float
    low: float
    first_five_index: int
    last_five_index: int


@dataclass(frozen=True, slots=True)
class RouteWindow:
    name: str
    source_name: str
    start_minute: int
    end_minute: int


ROUTE_WINDOWS = (
    RouteWindow("EUROPE_ROUTE", "ASIA_SOURCE", 8 * 60, 13 * 60),
    RouteWindow("US_ROUTE", "EUROPE_SOURCE", 13 * 60, 18 * 60),
)
SOURCE_WINDOWS = {
    "ASIA_SOURCE": (0, 8 * 60),
    "EUROPE_SOURCE": (8 * 60, 13 * 60),
}


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    scenario_id: str
    family: str
    route_name: str
    source_name: str
    route_start_ns: int
    route_end_ns: int
    direction: int
    draw: DrawContext
    target: ExternalLevel
    boundary_id: str
    boundary_source: str
    boundary_level: float
    interaction_time_ns: int
    trigger_time_ns: int
    trigger_five_index: int
    structural_reference: float
    interaction_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeliverySetup:
    swing: Swing
    displacement_time_ns: int
    confirmation_time_ns: int
    confirmation_five_index: int
    fvg_low: float
    fvg_high: float


def bucket_start_ns(ns: int, bucket_ns: int) -> int:
    return int(ns) // int(bucket_ns) * int(bucket_ns)


def day_start_ns(ns: int) -> int:
    return bucket_start_ns(ns, ONE_DAY_NS)


def minute_of_day(ns: int) -> int:
    stamp = pd.Timestamp(int(ns), unit="ns", tz="UTC")
    return int(stamp.hour) * 60 + int(stamp.minute)


def _prior_median(values: list[float], position: int, lookback: int) -> float:
    prior = [value for value in values[max(0, position - lookback):position] if isfinite(value)]
    if len(prior) < min(6, lookback):
        return float("nan")
    return float(np.median(np.asarray(prior, dtype=float)))


def _true_range(high: float, low: float, previous_close: float | None) -> float:
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def aggregate_four_hour_bars(
    bars: tuple[FiveMinuteBar, ...],
    config: DayLiquidityDeliveryConfig,
) -> tuple[FourHourBar, ...]:
    grouped: dict[int, list[FiveMinuteBar]] = {}
    for bar in bars:
        grouped.setdefault(bucket_start_ns(bar.ts_event_ns, FOUR_HOURS_NS), []).append(bar)
    groups: list[list[FiveMinuteBar]] = []
    for _, rows in sorted(grouped.items()):
        rows.sort(key=lambda item: item.index)
        if len(rows) == 48 and [r.index for r in rows] == list(range(rows[0].index, rows[0].index + 48)):
            groups.append(rows)

    bodies: list[float] = []
    ranges: list[float] = []
    true_ranges: list[float] = []
    previous_close: float | None = None
    result: list[FourHourBar] = []
    for index, rows in enumerate(groups):
        open_price, close = rows[0].open, rows[-1].close
        high, low = max(r.high for r in rows), min(r.low for r in rows)
        tr = _true_range(high, low, previous_close)
        atr_prior = true_ranges[max(0, index - 14):index]
        result.append(
            FourHourBar(
                index=index,
                end_time_ns=rows[-1].ts_event_ns,
                last_five_index=rows[-1].index,
                open=open_price,
                high=high,
                low=low,
                close=close,
                atr=float(np.mean(atr_prior)) if len(atr_prior) >= 6 else float("nan"),
                prior_body_median=_prior_median(bodies, index, config.h4_displacement_lookback),
                prior_range_median=_prior_median(ranges, index, config.h4_displacement_lookback),
            )
        )
        bodies.append(abs(close - open_price))
        ranges.append(high - low)
        true_ranges.append(tr)
        previous_close = close
    return tuple(result)


def aggregate_fifteen_minute_bars(
    bars: tuple[FiveMinuteBar, ...],
    config: DayLiquidityDeliveryConfig,
) -> tuple[FifteenMinuteBar, ...]:
    grouped: dict[int, list[FiveMinuteBar]] = {}
    for bar in bars:
        grouped.setdefault(bucket_start_ns(bar.ts_event_ns, FIFTEEN_MINUTES_NS), []).append(bar)
    groups: list[tuple[int, list[FiveMinuteBar]]] = []
    for start, rows in sorted(grouped.items()):
        rows.sort(key=lambda item: item.index)
        if len(rows) == 3 and [r.index for r in rows] == list(range(rows[0].index, rows[0].index + 3)):
            groups.append((start, rows))

    bodies: list[float] = []
    ranges: list[float] = []
    true_ranges: list[float] = []
    previous_close: float | None = None
    result: list[FifteenMinuteBar] = []
    for index, (start, rows) in enumerate(groups):
        open_price, close = rows[0].open, rows[-1].close
        high, low = max(r.high for r in rows), min(r.low for r in rows)
        tr = _true_range(high, low, previous_close)
        atr_prior = true_ranges[max(0, index - 20):index]
        result.append(
            FifteenMinuteBar(
                index=index,
                start_time_ns=start,
                end_time_ns=rows[-1].ts_event_ns,
                first_five_index=rows[0].index,
                last_five_index=rows[-1].index,
                open=open_price,
                high=high,
                low=low,
                close=close,
                atr=float(np.mean(atr_prior)) if len(atr_prior) >= 6 else float("nan"),
                prior_body_median=_prior_median(bodies, index, config.h4_displacement_lookback),
                prior_range_median=_prior_median(ranges, index, config.h4_displacement_lookback),
            )
        )
        bodies.append(abs(close - open_price))
        ranges.append(high - low)
        true_ranges.append(tr)
        previous_close = close
    return tuple(result)


def is_swing_high(values: list[float], center: int, span: int) -> bool:
    return all(values[center] > values[i] for i in range(center - span, center + span + 1) if i != center)


def is_swing_low(values: list[float], center: int, span: int) -> bool:
    return all(values[center] < values[i] for i in range(center - span, center + span + 1) if i != center)


def confirmed_five_swings(
    bars: tuple[FiveMinuteBar, ...], span: int
) -> tuple[tuple[Swing, ...], tuple[Swing, ...]]:
    highs, lows = [b.high for b in bars], [b.low for b in bars]
    high_swings: list[Swing] = []
    low_swings: list[Swing] = []
    for confirmed in range(2 * span, len(bars)):
        center = confirmed - span
        if is_swing_high(highs, center, span):
            high_swings.append(Swing("FIVE_HIGH", bars[center].high, center, bars[center].ts_event_ns, confirmed, bars[confirmed].ts_event_ns))
        if is_swing_low(lows, center, span):
            low_swings.append(Swing("FIVE_LOW", bars[center].low, center, bars[center].ts_event_ns, confirmed, bars[confirmed].ts_event_ns))
    return tuple(high_swings), tuple(low_swings)


def five_prior_medians(
    bars: tuple[FiveMinuteBar, ...], lookback: int
) -> tuple[np.ndarray, np.ndarray]:
    bodies = np.asarray([abs(b.close - b.open) for b in bars], dtype=float)
    ranges = np.asarray([b.high - b.low for b in bars], dtype=float)
    body_med = np.full(len(bars), np.nan)
    range_med = np.full(len(bars), np.nan)
    for position in range(len(bars)):
        start = max(0, position - lookback)
        if position - start >= min(6, lookback):
            body_med[position] = float(np.median(bodies[start:position]))
            range_med[position] = float(np.median(ranges[start:position]))
    return body_med, range_med


def build_session_ranges(bars: tuple[FiveMinuteBar, ...]) -> dict[tuple[int, str], SessionRange]:
    by_day: dict[int, list[FiveMinuteBar]] = {}
    for bar in bars:
        by_day.setdefault(day_start_ns(bar.ts_event_ns), []).append(bar)
    result: dict[tuple[int, str], SessionRange] = {}
    for day, rows in by_day.items():
        for name, (start, end) in SOURCE_WINDOWS.items():
            selected = sorted(
                (r for r in rows if start <= minute_of_day(r.ts_event_ns) < end),
                key=lambda item: item.index,
            )
            expected = (end - start) // 5
            if len(selected) != expected or [r.index for r in selected] != list(range(selected[0].index, selected[0].index + expected)):
                continue
            result[(day, name)] = SessionRange(
                day,
                name,
                max(r.high for r in selected),
                min(r.low for r in selected),
                selected[0].index,
                selected[-1].index,
            )
    return result


def route_window_for_bar(bar: FifteenMinuteBar) -> RouteWindow | None:
    minute = minute_of_day(bar.start_time_ns)
    return next((route for route in ROUTE_WINDOWS if route.start_minute <= minute < route.end_minute), None)


def first_execution_position_after(data_times: np.ndarray, completed_ns: int) -> int | None:
    position = int(np.searchsorted(data_times, int(completed_ns), side="right"))
    return position if 0 <= position < len(data_times) else None


def entry_is_in_draw_location(direction: int, entry: float, origin: float, target: float) -> bool:
    if direction > 0:
        return origin < entry < target and entry <= (origin + target) / 2.0
    return target < entry < origin and entry >= (origin + target) / 2.0


__all__ = [
    "ACCEPTANCE_FAMILY", "DayLiquidityDeliveryConfig", "DeliverySetup", "DrawContext",
    "FifteenMinuteBar", "FourHourBar", "IMPLEMENTATION_REVISION", "RAID_FAMILY",
    "RouteCandidate", "SessionRange", "Swing", "aggregate_fifteen_minute_bars",
    "aggregate_four_hour_bars", "build_session_ranges", "confirmed_five_swings",
    "day_start_ns", "entry_is_in_draw_location", "first_execution_position_after",
    "five_prior_medians", "is_swing_high", "is_swing_low", "minute_of_day",
    "route_window_for_bar",
]
