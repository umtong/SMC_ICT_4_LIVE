"""Causal swing-liquidity objectives for EasyChart v3.

The EasyChart material repeatedly uses prior meaningful highs/lows as liquidity
and as fixed objectives.  This detector exposes only *confirmed* wick pivots:
a pivot is unavailable until the required bars on its right have closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from domain import Candle
from easychart_zones import ZoneSide


class ObjectiveKind(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"


@dataclass(slots=True)
class ObjectiveZone:
    zone_id: str
    kind: ObjectiveKind
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
    pivot_span: int
    consumed: bool = False
    consumed_time_ns: int | None = None

    def __post_init__(self) -> None:
        values = (
            self.lower,
            self.upper,
            self.invalidation,
            self.impulse_extreme,
            self.strength_ratio,
        )
        if self.timeframe_minutes <= 0 or self.pivot_span <= 0:
            raise ValueError("timeframe and pivot span must be positive")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("objective values must be finite")
        if not self.lower < self.upper:
            raise ValueError("objective lower must be below upper")

    @property
    def active(self) -> bool:
        return not self.consumed


class CausalLiquidityDetector:
    """Confirmed wick pivots with first-touch lifecycle.

    No future bar is read before its close. Multiple spans are retained because
    a local swing and a larger auction objective solve different decisions.
    Only currently unspent objectives are scanned on each price observation.
    """

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        tick_size: float,
        *,
        pivot_spans: tuple[int, ...] = (2, 6),
    ) -> None:
        if timeframe_minutes <= 0 or tick_size <= 0.0:
            raise ValueError("timeframe and tick size must be positive")
        if not pivot_spans or any(span <= 0 for span in pivot_spans):
            raise ValueError("pivot spans must contain positive integers")
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.tick_size = tick_size
        self.pivot_spans = tuple(sorted(set(pivot_spans)))
        self.bars: list[Candle] = []
        self.zones: list[ObjectiveZone] = []
        self._active: dict[str, ObjectiveZone] = {}
        self.diagnostics: dict[str, int] = {}
        self._last_observed_price_time_ns = -1

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def observe_price(self, bar: Candle) -> None:
        """Consume objectives with any causally later bar, regardless of source TF."""
        if bar.ts_close_ns < self._last_observed_price_time_ns:
            raise ValueError("price observations must be nondecreasing")
        self._last_observed_price_time_ns = bar.ts_close_ns
        for zone_id, zone in list(self._active.items()):
            if bar.ts_close_ns <= zone.observed_time_ns:
                continue
            hit = bar.high >= zone.lower if zone.side is ZoneSide.RESISTANCE else bar.low <= zone.upper
            if hit:
                zone.consumed = True
                zone.consumed_time_ns = bar.ts_close_ns
                self._active.pop(zone_id, None)
                self._inc(f"{zone.kind.value.lower()}_consumed")

    def _zone_id(self, kind: ObjectiveKind, center: int, span: int) -> str:
        return f"{self.symbol}:{self.timeframe_minutes}m:{kind.value}:{center}:s{span}"

    def _create(self, *, center: int, span: int, kind: ObjectiveKind, observed: Candle) -> None:
        pivot = self.bars[center]
        if kind is ObjectiveKind.SWING_HIGH:
            level = pivot.high
            side = ZoneSide.RESISTANCE
            lower, upper = level, level + self.tick_size
            invalidation = upper + self.tick_size
        else:
            level = pivot.low
            side = ZoneSide.SUPPORT
            lower, upper = level - self.tick_size, level
            invalidation = lower - self.tick_size
        zone_id = self._zone_id(kind, center, span)
        if any(zone.zone_id == zone_id for zone in self.zones):
            return
        left = self.bars[center - span : center]
        right = self.bars[center + 1 : center + span + 1]
        if kind is ObjectiveKind.SWING_HIGH:
            prominence = min(
                level - min(bar.low for bar in left),
                level - min(bar.low for bar in right),
            )
        else:
            prominence = min(
                max(bar.high for bar in left) - level,
                max(bar.high for bar in right) - level,
            )
        avg_range = sum(bar.high - bar.low for bar in left + right) / max(len(left) + len(right), 1)
        strength = prominence / max(avg_range, self.tick_size)
        zone = ObjectiveZone(
            zone_id=zone_id,
            kind=kind,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=level,
            formed_index=center,
            formed_time_ns=pivot.ts_close_ns,
            observed_time_ns=observed.ts_close_ns,
            formation_indices=(center,),
            strength_ratio=strength,
            pivot_span=span,
        )
        self.zones.append(zone)
        self._active[zone.zone_id] = zone
        self._inc(f"{kind.value.lower()}_confirmed")

    def on_bar(self, bar: Candle) -> list[ObjectiveZone]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("source bars must arrive in strictly increasing close time")
        self.observe_price(bar)
        self.bars.append(bar)
        observed_index = len(self.bars) - 1
        created_before = len(self.zones)
        for span in self.pivot_spans:
            center = observed_index - span
            if center < span:
                continue
            window = self.bars[center - span : center + span + 1]
            if len(window) != 2 * span + 1:
                continue
            pivot = self.bars[center]
            highs = [item.high for item in window]
            lows = [item.low for item in window]
            if pivot.high == max(highs) and highs.count(pivot.high) == 1:
                self._create(center=center, span=span, kind=ObjectiveKind.SWING_HIGH, observed=bar)
            if pivot.low == min(lows) and lows.count(pivot.low) == 1:
                self._create(center=center, span=span, kind=ObjectiveKind.SWING_LOW, observed=bar)
        return self.zones[created_before:]

    def active_zones(self, *, side: ZoneSide | None = None) -> list[ObjectiveZone]:
        return [zone for zone in self._active.values() if side is None or zone.side is side]
