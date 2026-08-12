"""Causal wick trendlines for EasyChart structure scenarios.

This module adapts a useful idea from programmatic trendline scanners: generate
line candidates from confirmed pivot pairs, then count/monitor only information
available at the current close.  It deliberately avoids fitting against future
bars or ranking lines by future reactions.

A line is a market object, not a trade signal.  It can later participate in a
bounce, breakout/retest or fakeout scenario.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median

from causal_swings import CausalSwingTracker, SwingPoint, SwingSide
from domain import Candle


class TrendLineSide(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class TrendLineState(str, Enum):
    ACTIVE = "ACTIVE"
    BROKEN = "BROKEN"
    RETESTED = "RETESTED"
    FAILED_BREAK = "FAILED_BREAK"


class TrendLineEventKind(str, Enum):
    CREATED = "CREATED"
    BREAK = "BREAK"
    FIRST_RETEST = "FIRST_RETEST"
    FAILED_BREAK = "FAILED_BREAK"


@dataclass(slots=True)
class CausalTrendLine:
    line_id: str
    side: TrendLineSide
    timeframe_minutes: int
    first_swing_id: str
    second_swing_id: str
    first_index: int
    second_index: int
    first_time_ns: int
    second_time_ns: int
    first_level: float
    second_level: float
    observed_index: int
    observed_time_ns: int
    slope_per_bar: float
    tolerance: float
    state: TrendLineState = TrendLineState.ACTIVE
    touch_count: int = 2
    last_touch_index: int | None = None
    break_index: int | None = None
    break_time_ns: int | None = None
    break_level: float | None = None
    break_extreme: float | None = None
    retest_index: int | None = None
    retest_time_ns: int | None = None
    retest_level: float | None = None

    def price_at(self, index: int) -> float:
        return self.first_level + self.slope_per_bar * (index - self.first_index)

    @property
    def anchor_span_bars(self) -> int:
        return self.second_index - self.first_index


@dataclass(frozen=True, slots=True)
class TrendLineEvent:
    kind: TrendLineEventKind
    line_id: str
    side: TrendLineSide
    index: int
    time_ns: int
    line_level: float
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float


class CausalTrendLineTracker:
    """Build and update trendlines from closed, causally confirmed wick pivots.

    The detector keeps the source material's essential geometry:

    - support connects rising wick lows;
    - resistance connects falling wick highs;
    - two confirmed anchors are enough to make a line observable;
    - a directional close through the line creates a break candidate;
    - only the first later touch which closes on the breakout side is a retest;
    - a close back through the line before that retest is a failed break, which
      belongs to a fakeout family rather than the breakout family.

    ``tolerance_range_fraction`` is measurement tolerance, not an alpha score.
    It scales the visual thickness of a line by the median closed-bar range that
    was available when the second anchor became observable.  The value is stored
    on the line and never refitted from later outcomes.
    """

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        tick_size: float,
        *,
        swing_span: int = 2,
        min_anchor_bars: int = 3,
        scale_window: int = 24,
        tolerance_range_fraction: float = 0.10,
        max_prior_anchors: int = 16,
    ) -> None:
        if timeframe_minutes <= 0:
            raise ValueError("timeframe_minutes must be positive")
        if tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        if swing_span < 1:
            raise ValueError("swing_span must be positive")
        if min_anchor_bars < 1:
            raise ValueError("min_anchor_bars must be positive")
        if scale_window < 1:
            raise ValueError("scale_window must be positive")
        if not 0.0 <= tolerance_range_fraction <= 1.0:
            raise ValueError("tolerance_range_fraction must be in [0, 1]")
        if max_prior_anchors < 1:
            raise ValueError("max_prior_anchors must be positive")

        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.tick_size = tick_size
        self.min_anchor_bars = min_anchor_bars
        self.scale_window = scale_window
        self.tolerance_range_fraction = tolerance_range_fraction
        self.max_prior_anchors = max_prior_anchors
        self.swing_tracker = CausalSwingTracker(symbol, timeframe_minutes, swing_span)
        self.bars: list[Candle] = []
        self.lines: list[CausalTrendLine] = []
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    @staticmethod
    def _line_side(swing_side: SwingSide) -> TrendLineSide:
        return (
            TrendLineSide.SUPPORT
            if swing_side is SwingSide.LOW
            else TrendLineSide.RESISTANCE
        )

    def _line_id(self, first: SwingPoint, second: SwingPoint) -> str:
        side = self._line_side(second.side)
        return (
            f"{self.symbol}:{self.timeframe_minutes}m:TRENDLINE:{side.value}:"
            f"{first.event_index}-{second.event_index}"
        )

    def _tolerance(self, observed_index: int) -> float:
        start = max(0, observed_index - self.scale_window + 1)
        ranges = [bar.high - bar.low for bar in self.bars[start : observed_index + 1]]
        positive = [value for value in ranges if value > 0.0]
        scale = median(positive) if positive else self.tick_size
        return max(self.tick_size, scale * self.tolerance_range_fraction)

    @staticmethod
    def _direction_valid(first: SwingPoint, second: SwingPoint) -> bool:
        if first.side is not second.side:
            return False
        if second.side is SwingSide.LOW:
            return second.level > first.level
        return second.level < first.level

    def _not_already_broken(self, line: CausalTrendLine, through_index: int) -> bool:
        # Wick excursions are allowed because they can be fakeouts.  A closed
        # body beyond the line before the line was observable makes it unusable
        # as a live breakout boundary.
        for index in range(line.first_index, through_index + 1):
            level = line.price_at(index)
            bar = self.bars[index]
            if line.side is TrendLineSide.SUPPORT:
                if bar.close < level - line.tolerance:
                    return False
            elif bar.close > level + line.tolerance:
                return False
        return True

    def _create_lines(self, created_swings: list[SwingPoint]) -> list[TrendLineEvent]:
        events: list[TrendLineEvent] = []
        existing = {line.line_id for line in self.lines}
        for second in created_swings:
            previous = [
                swing
                for swing in self.swing_tracker.swings
                if swing.side is second.side and swing.event_index < second.event_index
            ][-self.max_prior_anchors :]
            for first in previous:
                if second.event_index - first.event_index < self.min_anchor_bars:
                    continue
                if not self._direction_valid(first, second):
                    continue
                line_id = self._line_id(first, second)
                if line_id in existing:
                    continue
                slope = (second.level - first.level) / (
                    second.event_index - first.event_index
                )
                line = CausalTrendLine(
                    line_id=line_id,
                    side=self._line_side(second.side),
                    timeframe_minutes=self.timeframe_minutes,
                    first_swing_id=first.swing_id,
                    second_swing_id=second.swing_id,
                    first_index=first.event_index,
                    second_index=second.event_index,
                    first_time_ns=first.event_time_ns,
                    second_time_ns=second.event_time_ns,
                    first_level=first.level,
                    second_level=second.level,
                    observed_index=second.observed_index,
                    observed_time_ns=second.observed_time_ns,
                    slope_per_bar=slope,
                    tolerance=self._tolerance(second.observed_index),
                    last_touch_index=second.event_index,
                )
                if not self._not_already_broken(line, second.observed_index):
                    self._inc("candidate_broken_before_observable")
                    continue
                self.lines.append(line)
                existing.add(line_id)
                self._inc("line_created")
                bar = self.bars[second.observed_index]
                events.append(
                    TrendLineEvent(
                        kind=TrendLineEventKind.CREATED,
                        line_id=line.line_id,
                        side=line.side,
                        index=second.observed_index,
                        time_ns=bar.ts_close_ns,
                        line_level=line.price_at(second.observed_index),
                        bar_open=bar.open,
                        bar_high=bar.high,
                        bar_low=bar.low,
                        bar_close=bar.close,
                    ),
                )
        return events

    @staticmethod
    def _touches(bar: Candle, level: float, tolerance: float) -> bool:
        return bar.low <= level + tolerance and bar.high >= level - tolerance

    def _update_lines(self, index: int, bar: Candle) -> list[TrendLineEvent]:
        events: list[TrendLineEvent] = []
        for line in self.lines:
            if index <= line.observed_index:
                continue
            level = line.price_at(index)
            touches = self._touches(bar, level, line.tolerance)

            if line.state is TrendLineState.ACTIVE:
                if touches:
                    valid_side_close = (
                        bar.close >= level - line.tolerance
                        if line.side is TrendLineSide.SUPPORT
                        else bar.close <= level + line.tolerance
                    )
                    if valid_side_close and (
                        line.last_touch_index is None or index > line.last_touch_index + 1
                    ):
                        line.touch_count += 1
                        line.last_touch_index = index
                        self._inc("line_touch_episode")

                broke = (
                    line.side is TrendLineSide.RESISTANCE
                    and bar.close > level + line.tolerance
                    and bar.close > bar.open
                ) or (
                    line.side is TrendLineSide.SUPPORT
                    and bar.close < level - line.tolerance
                    and bar.close < bar.open
                )
                if not broke:
                    continue
                line.state = TrendLineState.BROKEN
                line.break_index = index
                line.break_time_ns = bar.ts_close_ns
                line.break_level = level
                line.break_extreme = (
                    bar.high if line.side is TrendLineSide.RESISTANCE else bar.low
                )
                self._inc("line_break")
                events.append(
                    TrendLineEvent(
                        kind=TrendLineEventKind.BREAK,
                        line_id=line.line_id,
                        side=line.side,
                        index=index,
                        time_ns=bar.ts_close_ns,
                        line_level=level,
                        bar_open=bar.open,
                        bar_high=bar.high,
                        bar_low=bar.low,
                        bar_close=bar.close,
                    ),
                )
                continue

            if line.state is not TrendLineState.BROKEN or line.break_index is None:
                continue
            if index <= line.break_index:
                continue

            valid_retest = touches and (
                (
                    line.side is TrendLineSide.RESISTANCE
                    and bar.close >= level - line.tolerance
                )
                or (
                    line.side is TrendLineSide.SUPPORT
                    and bar.close <= level + line.tolerance
                )
            )
            if valid_retest:
                line.state = TrendLineState.RETESTED
                line.retest_index = index
                line.retest_time_ns = bar.ts_close_ns
                line.retest_level = level
                self._inc("line_first_retest")
                events.append(
                    TrendLineEvent(
                        kind=TrendLineEventKind.FIRST_RETEST,
                        line_id=line.line_id,
                        side=line.side,
                        index=index,
                        time_ns=bar.ts_close_ns,
                        line_level=level,
                        bar_open=bar.open,
                        bar_high=bar.high,
                        bar_low=bar.low,
                        bar_close=bar.close,
                    ),
                )
                continue

            failed = (
                line.side is TrendLineSide.RESISTANCE
                and bar.close < level - line.tolerance
            ) or (
                line.side is TrendLineSide.SUPPORT
                and bar.close > level + line.tolerance
            )
            if failed:
                line.state = TrendLineState.FAILED_BREAK
                self._inc("line_failed_break")
                events.append(
                    TrendLineEvent(
                        kind=TrendLineEventKind.FAILED_BREAK,
                        line_id=line.line_id,
                        side=line.side,
                        index=index,
                        time_ns=bar.ts_close_ns,
                        line_level=level,
                        bar_open=bar.open,
                        bar_high=bar.high,
                        bar_low=bar.low,
                        bar_close=bar.close,
                    ),
                )
        return events

    def on_bar(self, bar: Candle) -> list[TrendLineEvent]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("bars must arrive in strictly increasing close time")
        self.bars.append(bar)
        created_swings = self.swing_tracker.on_bar(bar)
        index = len(self.bars) - 1
        events = self._create_lines(created_swings)
        events.extend(self._update_lines(index, bar))
        return events

    def line(self, line_id: str) -> CausalTrendLine | None:
        return next((line for line in self.lines if line.line_id == line_id), None)

    def active_lines(self, side: TrendLineSide | None = None) -> list[CausalTrendLine]:
        return [
            line
            for line in self.lines
            if line.state is TrendLineState.ACTIVE
            and (side is None or line.side is side)
        ]
