"""Causal local swing liquidity used by EasyChart entry scenarios."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domain import Candle


class SwingSide(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(slots=True)
class SwingPoint:
    swing_id: str
    side: SwingSide
    level: float
    event_index: int
    observed_index: int
    event_time_ns: int
    observed_time_ns: int
    span: int
    consumed: bool = False


class CausalSwingTracker:
    """Confirm wick pivots only after the right-side window has closed."""

    def __init__(self, symbol: str, timeframe_minutes: int, span: int = 2) -> None:
        if timeframe_minutes <= 0:
            raise ValueError("timeframe_minutes must be positive")
        if span < 1:
            raise ValueError("span must be positive")
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.span = span
        self.bars: list[Candle] = []
        self.swings: list[SwingPoint] = []
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _swing_id(self, side: SwingSide, center: int, level: float) -> str:
        return f"{self.symbol}:{self.timeframe_minutes}m:{side.value}:{center}:{self.span}:{level:.12g}"

    def on_bar(self, bar: Candle) -> list[SwingPoint]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("bars must arrive in strictly increasing close time")
        self.bars.append(bar)
        observed_index = len(self.bars) - 1
        center = observed_index - self.span
        if center < self.span:
            return []
        window = self.bars[center - self.span : center + self.span + 1]
        if len(window) != 2 * self.span + 1:
            return []
        pivot = self.bars[center]
        highs = [item.high for item in window]
        lows = [item.low for item in window]
        created: list[SwingPoint] = []
        if pivot.high == max(highs) and highs.count(pivot.high) == 1:
            swing = SwingPoint(
                swing_id=self._swing_id(SwingSide.HIGH, center, pivot.high),
                side=SwingSide.HIGH,
                level=pivot.high,
                event_index=center,
                observed_index=observed_index,
                event_time_ns=pivot.ts_close_ns,
                observed_time_ns=bar.ts_close_ns,
                span=self.span,
            )
            self.swings.append(swing)
            created.append(swing)
            self._inc("swing_high")
        if pivot.low == min(lows) and lows.count(pivot.low) == 1:
            swing = SwingPoint(
                swing_id=self._swing_id(SwingSide.LOW, center, pivot.low),
                side=SwingSide.LOW,
                level=pivot.low,
                event_index=center,
                observed_index=observed_index,
                event_time_ns=pivot.ts_close_ns,
                observed_time_ns=bar.ts_close_ns,
                span=self.span,
            )
            self.swings.append(swing)
            created.append(swing)
            self._inc("swing_low")
        return created

    def eligible_for_overlap(
        self,
        *,
        side: SwingSide,
        overlap_lower: float,
        overlap_upper: float,
        before_ns: int,
    ) -> list[SwingPoint]:
        """Return unconsumed local liquidity plausibly attached to an overlap.

        For support, the prior swing low may lie inside or just above the zone;
        for resistance, the prior swing high may lie inside or just below it.
        One overlap width is allowed beyond the near edge so a sweep can travel
        into the decision zone rather than requiring the swing itself inside it.
        """
        width = overlap_upper - overlap_lower
        if width <= 0.0:
            raise ValueError("overlap width must be positive")
        if side is SwingSide.LOW:
            lower_bound = overlap_lower
            upper_bound = overlap_upper + width
        else:
            lower_bound = overlap_lower - width
            upper_bound = overlap_upper
        return [
            swing
            for swing in self.swings
            if not swing.consumed
            and swing.side is side
            and swing.observed_time_ns < before_ns
            and lower_bound <= swing.level <= upper_bound
        ]

    def strongest_eligible(
        self,
        *,
        side: SwingSide,
        overlap_lower: float,
        overlap_upper: float,
        before_ns: int,
    ) -> SwingPoint | None:
        candidates = self.eligible_for_overlap(
            side=side,
            overlap_lower=overlap_lower,
            overlap_upper=overlap_upper,
            before_ns=before_ns,
        )
        if not candidates:
            return None
        # The latest *observed* swing is what a live trader can most recently
        # identify before the sweep; no future prominence ranking is used.
        return max(candidates, key=lambda item: (item.observed_time_ns, item.event_time_ns))
