"""Causal EasyChart order-block, FVG and multi-timeframe zone semantics.

This module deliberately does not submit orders. It first makes the source
material's chart objects explicit and testable so a bad result cannot be
mistaken for evidence against a concept which was never encoded correctly.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from domain import Candle


class ZoneKind(str, Enum):
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"


class ZoneSide(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


@dataclass(slots=True)
class PriceZone:
    zone_id: str
    kind: ZoneKind
    side: ZoneSide
    timeframe_minutes: int
    lower: float
    upper: float
    invalidation: float
    impulse_extreme: float
    formed_index: int
    formed_time_ns: int
    observed_time_ns: int
    formation_indices: tuple[int, ...]
    strength_ratio: float
    source_body_lower: float | None = None
    source_body_upper: float | None = None
    first_touch_index: int | None = None
    first_touch_time_ns: int | None = None
    invalidated_index: int | None = None
    invalidated_time_ns: int | None = None
    consumed: bool = False

    def __post_init__(self) -> None:
        if self.timeframe_minutes <= 0:
            raise ValueError("timeframe_minutes must be positive")
        values = (
            self.lower,
            self.upper,
            self.invalidation,
            self.impulse_extreme,
            self.strength_ratio,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("zone values must be finite")
        if not self.lower < self.upper:
            raise ValueError("zone lower must be below upper")
        if self.side is ZoneSide.SUPPORT and not self.invalidation < self.upper:
            raise ValueError("support invalidation must be below the zone")
        if self.side is ZoneSide.RESISTANCE and not self.invalidation > self.lower:
            raise ValueError("resistance invalidation must be above the zone")

    @property
    def active(self) -> bool:
        return self.invalidated_index is None and not self.consumed

    @property
    def high_quality_by_size(self) -> bool:
        # The source material calls a 2x or larger body difference reliable for
        # OBs, and a 2–3x middle candle meaningful for FVGs.
        return self.strength_ratio + 1e-12 >= 2.0

    def overlaps(self, other: "PriceZone") -> bool:
        return self.side is other.side and max(self.lower, other.lower) < min(self.upper, other.upper)


@dataclass(frozen=True, slots=True)
class ZoneOverlap:
    overlap_id: str
    side: ZoneSide
    lower: float
    upper: float
    higher_zone_id: str
    lower_zone_id: str
    higher_timeframe_minutes: int
    lower_timeframe_minutes: int
    observed_time_ns: int

    def __post_init__(self) -> None:
        if not self.lower < self.upper:
            raise ValueError("overlap lower must be below upper")
        if self.higher_timeframe_minutes <= self.lower_timeframe_minutes:
            raise ValueError("higher timeframe must actually be higher")


class EasyChartZoneDetector:
    """Detect source-defined OB/FVG zones from closed candles only.

    Order block
        Opposite candle body engulfed by the current candle body. The zone is
        the *engulfed candle body*, as stated in the video transcript, while
        invalidation uses the wick extreme of all formation candles.

    FVG
        A three-candle non-overlap where the middle directional body is at
        least twice the larger adjacent body. The zone is the actual wick gap.
    """

    def __init__(self, symbol: str, timeframe_minutes: int, tick_size: float) -> None:
        if timeframe_minutes <= 0:
            raise ValueError("timeframe_minutes must be positive")
        if tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.tick_size = tick_size
        self.bars: list[Candle] = []
        self.zones: list[PriceZone] = []
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    @staticmethod
    def _body(bar: Candle) -> tuple[float, float, float]:
        lower = min(bar.open, bar.close)
        upper = max(bar.open, bar.close)
        return lower, upper, upper - lower

    def _ratio(self, numerator: float, denominator: float) -> float:
        # An adjacent doji does not make the semantic ratio undefined. Use one
        # tradable price increment as the minimum measurable body so the ratio
        # remains finite, auditable and comparable across instruments.
        return numerator / max(denominator, self.tick_size)

    def _zone_id(self, kind: ZoneKind, side: ZoneSide, indices: tuple[int, ...]) -> str:
        joined = "-".join(str(index) for index in indices)
        return f"{self.symbol}:{self.timeframe_minutes}m:{kind.value}:{side.value}:{joined}"

    def _update_lifecycle(self, current: Candle, index: int) -> None:
        # Formation candles cannot mitigate their own zone. Only a later closed
        # candle may touch or invalidate it. An exact stop-price touch is an
        # invalidation because a live stop order would execute there.
        for zone in self.zones:
            if not zone.active or index <= zone.formed_index:
                continue
            if zone.side is ZoneSide.SUPPORT:
                invalidated = current.low <= zone.invalidation
                touched = current.low <= zone.upper and current.high >= zone.lower
            else:
                invalidated = current.high >= zone.invalidation
                touched = current.high >= zone.lower and current.low <= zone.upper
            if invalidated:
                zone.invalidated_index = index
                zone.invalidated_time_ns = current.ts_close_ns
                self._inc(f"{zone.kind.value.lower()}_invalidated_before_or_on_touch")
                continue
            if touched and zone.first_touch_index is None:
                zone.first_touch_index = index
                zone.first_touch_time_ns = current.ts_close_ns
                self._inc(f"{zone.kind.value.lower()}_first_touch")

    def _detect_order_block(self, index: int) -> PriceZone | None:
        if index < 1:
            return None
        previous = self.bars[index - 1]
        current = self.bars[index]
        previous_lower, previous_upper, previous_body = self._body(previous)
        current_lower, current_upper, current_body = self._body(current)
        if previous_body <= 0.0 or current_body <= 0.0:
            self._inc("order_block_doji_rejected")
            return None

        bullish = (
            previous.close < previous.open
            and current.close > current.open
            and current_lower <= previous_lower
            and current_upper >= previous_upper
        )
        bearish = (
            previous.close > previous.open
            and current.close < current.open
            and current_lower <= previous_lower
            and current_upper >= previous_upper
        )
        if not bullish and not bearish:
            return None

        side = ZoneSide.SUPPORT if bullish else ZoneSide.RESISTANCE
        formation = (index - 1, index)
        invalidation = (
            min(previous.low, current.low) - self.tick_size
            if bullish
            else max(previous.high, current.high) + self.tick_size
        )
        impulse_extreme = current.high if bullish else current.low
        zone = PriceZone(
            zone_id=self._zone_id(ZoneKind.ORDER_BLOCK, side, formation),
            kind=ZoneKind.ORDER_BLOCK,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            lower=previous_lower,
            upper=previous_upper,
            invalidation=invalidation,
            impulse_extreme=impulse_extreme,
            formed_index=index,
            formed_time_ns=previous.ts_close_ns,
            observed_time_ns=current.ts_close_ns,
            formation_indices=formation,
            strength_ratio=self._ratio(current_body, previous_body),
            source_body_lower=previous_lower,
            source_body_upper=previous_upper,
        )
        self._inc("order_block_detected")
        if zone.high_quality_by_size:
            self._inc("order_block_size_confirmed")
        else:
            self._inc("order_block_below_two_x")
        return zone

    def _detect_fvg(self, index: int) -> PriceZone | None:
        if index < 2:
            return None
        first = self.bars[index - 2]
        middle = self.bars[index - 1]
        third = self.bars[index]
        _, _, first_body = self._body(first)
        _, _, middle_body = self._body(middle)
        _, _, third_body = self._body(third)
        ratio = self._ratio(middle_body, max(first_body, third_body))

        bullish_gap = first.high < third.low and middle.close > middle.open
        bearish_gap = first.low > third.high and middle.close < middle.open
        if not bullish_gap and not bearish_gap:
            return None
        if ratio + 1e-12 < 2.0:
            self._inc("fvg_middle_body_below_two_x")
            return None

        side = ZoneSide.SUPPORT if bullish_gap else ZoneSide.RESISTANCE
        formation = (index - 2, index - 1, index)
        if bullish_gap:
            lower, upper = first.high, third.low
            invalidation = min(first.low, middle.low, third.low) - self.tick_size
            impulse_extreme = max(first.high, middle.high, third.high)
        else:
            lower, upper = third.high, first.low
            invalidation = max(first.high, middle.high, third.high) + self.tick_size
            impulse_extreme = min(first.low, middle.low, third.low)
        zone = PriceZone(
            zone_id=self._zone_id(ZoneKind.FVG, side, formation),
            kind=ZoneKind.FVG,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=impulse_extreme,
            formed_index=index,
            formed_time_ns=middle.ts_close_ns,
            observed_time_ns=third.ts_close_ns,
            formation_indices=formation,
            strength_ratio=ratio,
        )
        self._inc("fvg_detected")
        return zone

    def on_bar(self, bar: Candle) -> list[PriceZone]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("bars must arrive in strictly increasing close time")
        self.bars.append(bar)
        index = len(self.bars) - 1
        self._update_lifecycle(bar, index)
        created: list[PriceZone] = []
        order_block = self._detect_order_block(index)
        if order_block is not None:
            self.zones.append(order_block)
            created.append(order_block)
        fvg = self._detect_fvg(index)
        if fvg is not None:
            self.zones.append(fvg)
            created.append(fvg)
        return created

    def active_zones(
        self,
        *,
        side: ZoneSide | None = None,
        kind: ZoneKind | None = None,
        high_quality_only: bool = False,
    ) -> list[PriceZone]:
        return [
            zone
            for zone in self.zones
            if zone.active
            and (side is None or zone.side is side)
            and (kind is None or zone.kind is kind)
            and (not high_quality_only or zone.high_quality_by_size)
        ]


def overlap_zones(higher: PriceZone, lower: PriceZone) -> ZoneOverlap | None:
    """Return the actual price intersection of same-side cross-timeframe zones."""
    if higher.timeframe_minutes <= lower.timeframe_minutes:
        raise ValueError("first zone must be from a higher timeframe")
    if not higher.active or not lower.active or not higher.overlaps(lower):
        return None
    intersection_lower = max(higher.lower, lower.lower)
    intersection_upper = min(higher.upper, lower.upper)
    return ZoneOverlap(
        overlap_id=f"MTF:{higher.zone_id}|{lower.zone_id}",
        side=higher.side,
        lower=intersection_lower,
        upper=intersection_upper,
        higher_zone_id=higher.zone_id,
        lower_zone_id=lower.zone_id,
        higher_timeframe_minutes=higher.timeframe_minutes,
        lower_timeframe_minutes=lower.timeframe_minutes,
        observed_time_ns=max(higher.observed_time_ns, lower.observed_time_ns),
    )
