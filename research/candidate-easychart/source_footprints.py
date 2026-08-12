"""Source-faithful OB and FVG observations for semantic diagnostics.

These detectors do not trade.  They enumerate what the EasyChart PDFs and VTT
actually describe so an experiment can answer whether it contained the claimed
institutional footprint.

Order blocks
------------
* two-candle body engulf: the engulfed candle body is the zone;
* three-candle double engulf: the middle candle body is the zone;
* invalidation uses the extreme of every candle in the formation;
* the source's roughly two-times body-size statement is recorded as a quality
  attribute, not silently turned into a mandatory rule;
* the near-doji exception has no numeric source boundary, so only an exact doji
  is identifiable without inventing a threshold.

Fair-value gaps
---------------
* three completed candles;
* wick-to-wick gap between candles one and three;
* directional middle candle;
* middle body relative to both neighboring bodies is recorded, with the
  source-suggested 2x threshold exposed separately.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from domain_v3 import Candle, Side


@dataclass(frozen=True, slots=True)
class SourceOrderBlock:
    footprint_id: str
    symbol: str
    side: Side
    pattern: str
    timeframe_minutes: int
    observed_time_ns: int
    formation_start_ns: int
    formation_end_ns: int
    zone_low: float
    zone_high: float
    invalidation: float
    formation_low: float
    formation_high: float
    engulfed_body: float
    engulfing_body: float
    body_ratio: float
    source_two_x_quality: bool
    exact_doji_exception: bool
    numeric_doji_boundary_status: str

    def __post_init__(self) -> None:
        values = (
            self.zone_low,
            self.zone_high,
            self.invalidation,
            self.formation_low,
            self.formation_high,
            self.engulfed_body,
            self.engulfing_body,
            self.body_ratio,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("order-block values must be finite")
        if self.zone_high < self.zone_low:
            raise ValueError("invalid OB zone")


@dataclass(frozen=True, slots=True)
class SourceFVG:
    footprint_id: str
    symbol: str
    side: Side
    timeframe_minutes: int
    observed_time_ns: int
    formation_start_ns: int
    formation_end_ns: int
    zone_low: float
    zone_high: float
    formation_low: float
    formation_high: float
    middle_body: float
    first_body: float
    third_body: float
    ratio_to_first: float
    ratio_to_third: float
    minimum_neighbor_ratio: float
    source_two_x_quality: bool

    def __post_init__(self) -> None:
        if not self.zone_high > self.zone_low:
            raise ValueError("FVG must have positive wick gap")


@dataclass(frozen=True, slots=True)
class FootprintUpdate:
    order_blocks: tuple[SourceOrderBlock, ...]
    fvgs: tuple[SourceFVG, ...]


def body_bounds(candle: Candle) -> tuple[float, float]:
    return min(candle.open, candle.close), max(candle.open, candle.close)


def body_size(candle: Candle) -> float:
    return abs(candle.close - candle.open)


def direction(candle: Candle) -> int:
    if candle.close > candle.open:
        return 1
    if candle.close < candle.open:
        return -1
    return 0


def body_engulfs(outer: Candle, inner: Candle) -> bool:
    outer_low, outer_high = body_bounds(outer)
    inner_low, inner_high = body_bounds(inner)
    return (
        outer_low <= inner_low
        and outer_high >= inner_high
        and (outer_low < inner_low or outer_high > inner_high)
    )


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0.0:
        return float("inf") if numerator > 0.0 else 1.0
    return numerator / denominator


def _order_block(
    *,
    symbol: str,
    side: Side,
    pattern: str,
    timeframe_minutes: int,
    formation: Sequence[Candle],
    zone_candle: Candle,
    engulfed_body: float,
    engulfing_body: float,
) -> SourceOrderBlock:
    zone_low, zone_high = body_bounds(zone_candle)
    formation_low = min(candle.low for candle in formation)
    formation_high = max(candle.high for candle in formation)
    ratio = _safe_ratio(engulfing_body, engulfed_body)
    return SourceOrderBlock(
        footprint_id=(
            f"OB:{symbol}:{timeframe_minutes}:{pattern}:"
            f"{formation[0].ts_open_ns}:{formation[-1].ts_close_ns}:{side.name}"
        ),
        symbol=symbol,
        side=side,
        pattern=pattern,
        timeframe_minutes=timeframe_minutes,
        observed_time_ns=formation[-1].ts_close_ns,
        formation_start_ns=formation[0].ts_open_ns,
        formation_end_ns=formation[-1].ts_close_ns,
        zone_low=zone_low,
        zone_high=zone_high,
        invalidation=formation_low if side is Side.LONG else formation_high,
        formation_low=formation_low,
        formation_high=formation_high,
        engulfed_body=engulfed_body,
        engulfing_body=engulfing_body,
        body_ratio=ratio,
        source_two_x_quality=ratio >= 2.0,
        exact_doji_exception=engulfed_body == 0.0,
        numeric_doji_boundary_status=(
            "EXACT_DOJI_SOURCE_EXCEPTION"
            if engulfed_body == 0.0
            else "SOURCE_GIVES_NO_NUMERIC_NEAR_DOJI_BOUNDARY"
        ),
    )


def detect_order_blocks(
    symbol: str,
    candles: Sequence[Candle],
    timeframe_minutes: int,
) -> list[SourceOrderBlock]:
    output: list[SourceOrderBlock] = []
    for index in range(1, len(candles)):
        previous, current = candles[index - 1], candles[index]
        previous_direction = direction(previous)
        current_direction = direction(current)
        if (
            previous_direction != 0
            and current_direction == -previous_direction
            and body_engulfs(current, previous)
        ):
            side = Side.LONG if current_direction == 1 else Side.SHORT
            output.append(
                _order_block(
                    symbol=symbol,
                    side=side,
                    pattern="TWO_CANDLE_BODY_ENGULF",
                    timeframe_minutes=timeframe_minutes,
                    formation=(previous, current),
                    zone_candle=previous,
                    engulfed_body=body_size(previous),
                    engulfing_body=body_size(current),
                ),
            )

    # A double engulf is one stronger three-candle episode, not two unrelated
    # opportunities. The middle body is the source-stated zone.
    three_candle: list[SourceOrderBlock] = []
    final_keys: set[tuple[int, Side]] = set()
    for index in range(2, len(candles)):
        first, middle, final = candles[index - 2 : index + 1]
        directions = (direction(first), direction(middle), direction(final))
        if 0 in directions or not (
            directions[0] == directions[2]
            and directions[1] == -directions[0]
            and body_engulfs(middle, first)
            and body_engulfs(final, middle)
        ):
            continue
        side = Side.LONG if directions[2] == 1 else Side.SHORT
        three_candle.append(
            _order_block(
                symbol=symbol,
                side=side,
                pattern="THREE_CANDLE_DOUBLE_ENGULF_MIDDLE_BODY",
                timeframe_minutes=timeframe_minutes,
                formation=(first, middle, final),
                zone_candle=middle,
                engulfed_body=body_size(middle),
                engulfing_body=body_size(final),
            ),
        )
        final_keys.add((final.ts_close_ns, side))

    output = [
        item
        for item in output
        if (item.observed_time_ns, item.side) not in final_keys
    ]
    output.extend(three_candle)
    return sorted(output, key=lambda item: (item.observed_time_ns, item.footprint_id))


def detect_fvgs(
    symbol: str,
    candles: Sequence[Candle],
    timeframe_minutes: int,
) -> list[SourceFVG]:
    output: list[SourceFVG] = []
    for index in range(2, len(candles)):
        first, middle, third = candles[index - 2 : index + 1]
        middle_direction = direction(middle)
        if middle_direction == 1 and first.high < third.low:
            side = Side.LONG
            zone_low, zone_high = first.high, third.low
        elif middle_direction == -1 and first.low > third.high:
            side = Side.SHORT
            zone_low, zone_high = third.high, first.low
        else:
            continue
        middle_size = body_size(middle)
        first_size = body_size(first)
        third_size = body_size(third)
        ratio_first = _safe_ratio(middle_size, first_size)
        ratio_third = _safe_ratio(middle_size, third_size)
        minimum_ratio = min(ratio_first, ratio_third)
        formation = (first, middle, third)
        output.append(
            SourceFVG(
                footprint_id=(
                    f"FVG:{symbol}:{timeframe_minutes}:"
                    f"{first.ts_open_ns}:{third.ts_close_ns}:{side.name}"
                ),
                symbol=symbol,
                side=side,
                timeframe_minutes=timeframe_minutes,
                observed_time_ns=third.ts_close_ns,
                formation_start_ns=first.ts_open_ns,
                formation_end_ns=third.ts_close_ns,
                zone_low=zone_low,
                zone_high=zone_high,
                formation_low=min(candle.low for candle in formation),
                formation_high=max(candle.high for candle in formation),
                middle_body=middle_size,
                first_body=first_size,
                third_body=third_size,
                ratio_to_first=ratio_first,
                ratio_to_third=ratio_third,
                minimum_neighbor_ratio=minimum_ratio,
                source_two_x_quality=minimum_ratio >= 2.0,
            ),
        )
    return output


def detect_source_footprints(
    symbol: str,
    candles: Sequence[Candle],
    timeframe_minutes: int,
) -> FootprintUpdate:
    return FootprintUpdate(
        order_blocks=tuple(detect_order_blocks(symbol, candles, timeframe_minutes)),
        fvgs=tuple(detect_fvgs(symbol, candles, timeframe_minutes)),
    )


__all__ = [
    "FootprintUpdate",
    "SourceFVG",
    "SourceOrderBlock",
    "body_bounds",
    "body_engulfs",
    "body_size",
    "detect_fvgs",
    "detect_order_blocks",
    "detect_source_footprints",
]
