"""Causal wick trendlines for EasyChart structure scenarios.

The source asks for meaningful wick anchors, but does not define an exhaustive
pair search. Generating every direction-valid pivot pair produced thousands of
near-duplicate lines and made the implementation unlike the visually dominant
boundary a human actually follows.

This module instead maintains a causal outer envelope of confirmed wick pivots:

- confirmed swing highs update an upper monotone hull;
- confirmed swing lows update a lower monotone hull;
- only the newest hull edge can become a new trendline;
- every intervening wick must remain on the valid side of that edge;
- an already active line absorbs a later collinear touch rather than spawning a
  duplicate candidate.

A line is a market object, not a trade signal. It can later participate in a
bounce, breakout/retest or failed-break/fakeout scenario.
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
    EXPIRED_WITHOUT_RETEST = "EXPIRED_WITHOUT_RETEST"


class TrendLineEventKind(str, Enum):
    CREATED = "CREATED"
    BREAK = "BREAK"
    FIRST_RETEST = "FIRST_RETEST"
    FAILED_BREAK = "FAILED_BREAK"
    RETEST_MISSED = "RETEST_MISSED"


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
    continuation_extreme: float | None = None
    pullback_started_index: int | None = None
    pullback_started_time_ns: int | None = None
    pullback_reference_extreme: float | None = None
    retest_index: int | None = None
    retest_time_ns: int | None = None
    retest_level: float | None = None
    expired_index: int | None = None
    expired_time_ns: int | None = None

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

    ``tolerance_range_fraction`` is measurement tolerance, not an alpha score.
    It scales the visual thickness of a line by the median closed-bar range that
    was available when the second anchor became observable. The value is stored
    on the line and is never refitted from later outcomes.

    ``max_prior_anchors`` remains in the constructor for compatibility with the
    earlier all-pair detector. The outer-envelope implementation no longer uses
    a rolling candidate pool.
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
        self.hulls: dict[SwingSide, list[SwingPoint]] = {
            SwingSide.HIGH: [],
            SwingSide.LOW: [],
        }
        self.hull_reset_index: dict[SwingSide, int] = {
            SwingSide.HIGH: -1,
            SwingSide.LOW: -1,
        }

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
        ranges = [
            bar.high - bar.low
            for bar in self.bars[start : observed_index + 1]
            if bar.high > bar.low
        ]
        scale = median(ranges) if ranges else self.tick_size
        return max(self.tick_size, scale * self.tolerance_range_fraction)

    @staticmethod
    def _direction_valid(first: SwingPoint, second: SwingPoint) -> bool:
        if first.side is not second.side:
            return False
        if second.side is SwingSide.LOW:
            return second.level > first.level
        return second.level < first.level

    @staticmethod
    def _cross(first: SwingPoint, middle: SwingPoint, last: SwingPoint) -> float:
        return (
            (middle.event_index - first.event_index) * (last.level - first.level)
            - (middle.level - first.level) * (last.event_index - first.event_index)
        )

    def _update_hull(
        self,
        swing: SwingPoint,
    ) -> tuple[SwingPoint, SwingPoint] | None:
        if swing.event_index <= self.hull_reset_index[swing.side]:
            self._inc("swing_before_or_on_hull_reset_skipped")
            return None

        hull = self.hulls[swing.side]
        while len(hull) >= 2:
            cross = self._cross(hull[-2], hull[-1], swing)
            should_pop = (
                swing.side is SwingSide.HIGH and cross >= 0.0
            ) or (
                swing.side is SwingSide.LOW and cross <= 0.0
            )
            if not should_pop:
                break
            hull.pop()
            self._inc(
                "upper_hull_popped"
                if swing.side is SwingSide.HIGH
                else "lower_hull_popped",
            )
        hull.append(swing)
        if len(hull) < 2:
            return None
        return hull[-2], hull[-1]

    def _envelope_valid(self, line: CausalTrendLine, through_index: int) -> bool:
        """Require every known wick to remain inside the proposed outer edge."""
        for index in range(line.first_index, through_index + 1):
            level = line.price_at(index)
            bar = self.bars[index]
            if line.side is TrendLineSide.RESISTANCE:
                if bar.high > level + line.tolerance:
                    return False
            elif bar.low < level - line.tolerance:
                return False
        return True

    def _touch_marker(self, line: CausalTrendLine, index: int) -> bool:
        level = line.price_at(index)
        bar = self.bars[index]
        if line.side is TrendLineSide.RESISTANCE:
            return abs(bar.high - level) <= line.tolerance
        return abs(bar.low - level) <= line.tolerance

    def _historical_touch_episodes(
        self,
        line: CausalTrendLine,
    ) -> tuple[int, int | None]:
        touched = [
            index
            for index in range(line.first_index, line.observed_index + 1)
            if self._touch_marker(line, index)
        ]
        if not touched:
            return 0, None
        episodes = 1
        last = touched[0]
        for index in touched[1:]:
            if index > last + 1:
                episodes += 1
            last = index
        return episodes, last

    def _represented_by_active_line(
        self,
        swing: SwingPoint,
        tolerance: float,
    ) -> bool:
        side = self._line_side(swing.side)
        for line in self.lines:
            if line.state is not TrendLineState.ACTIVE or line.side is not side:
                continue
            if abs(line.price_at(swing.event_index) - swing.level) <= max(
                line.tolerance,
                tolerance,
            ):
                return True
        return False

    def _create_from_swing(self, swing: SwingPoint) -> list[TrendLineEvent]:
        edge = self._update_hull(swing)
        if edge is None:
            return []
        first, second = edge
        if second.event_index - first.event_index < self.min_anchor_bars:
            return []
        if not self._direction_valid(first, second):
            self._inc("hull_edge_wrong_direction")
            return []

        tolerance = self._tolerance(second.observed_index)
        if self._represented_by_active_line(second, tolerance):
            self._inc("active_line_represented_new_swing")
            return []

        slope = (second.level - first.level) / (
            second.event_index - first.event_index
        )
        line = CausalTrendLine(
            line_id=self._line_id(first, second),
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
            tolerance=tolerance,
        )
        if not self._envelope_valid(line, second.observed_index):
            # Preserve the old diagnostic key for longitudinal evidence while
            # making the stronger wick-envelope reason explicit.
            self._inc("candidate_broken_before_observable")
            self._inc("candidate_wick_cross_before_observable")
            return []
        if any(existing.line_id == line.line_id for existing in self.lines):
            return []

        line.touch_count, line.last_touch_index = self._historical_touch_episodes(line)
        self.lines.append(line)
        self._inc("line_created")
        bar = self.bars[second.observed_index]
        return [
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
        ]

    @staticmethod
    def _touches(bar: Candle, level: float, tolerance: float) -> bool:
        return bar.low <= level + tolerance and bar.high >= level - tolerance

    @staticmethod
    def _event(
        kind: TrendLineEventKind,
        line: CausalTrendLine,
        index: int,
        bar: Candle,
        level: float,
    ) -> TrendLineEvent:
        return TrendLineEvent(
            kind=kind,
            line_id=line.line_id,
            side=line.side,
            index=index,
            time_ns=bar.ts_close_ns,
            line_level=level,
            bar_open=bar.open,
            bar_high=bar.high,
            bar_low=bar.low,
            bar_close=bar.close,
        )

    def _reset_hull(self, line_side: TrendLineSide, index: int) -> None:
        swing_side = (
            SwingSide.HIGH
            if line_side is TrendLineSide.RESISTANCE
            else SwingSide.LOW
        )
        self.hulls[swing_side].clear()
        self.hull_reset_index[swing_side] = max(
            self.hull_reset_index[swing_side],
            index,
        )
        self._inc("hull_reset_after_break")

    def _advance_broken_line(
        self,
        line: CausalTrendLine,
        index: int,
        bar: Candle,
        level: float,
        touches: bool,
    ) -> list[TrendLineEvent]:
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
            return [
                self._event(
                    TrendLineEventKind.FIRST_RETEST,
                    line,
                    index,
                    bar,
                    level,
                ),
            ]

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
            return [
                self._event(
                    TrendLineEventKind.FAILED_BREAK,
                    line,
                    index,
                    bar,
                    level,
                ),
            ]

        broke_up = line.side is TrendLineSide.RESISTANCE
        if line.continuation_extreme is None:
            line.continuation_extreme = line.break_extreme
        assert line.continuation_extreme is not None

        if line.pullback_started_index is None:
            line.continuation_extreme = (
                max(line.continuation_extreme, bar.high)
                if broke_up
                else min(line.continuation_extreme, bar.low)
            )
            pullback_started = (
                broke_up and bar.close < bar.open
            ) or (
                not broke_up and bar.close > bar.open
            )
            if pullback_started:
                line.pullback_started_index = index
                line.pullback_started_time_ns = bar.ts_close_ns
                line.pullback_reference_extreme = line.continuation_extreme
                self._inc("line_first_pullback_started")
            return []

        assert line.pullback_reference_extreme is not None
        reference = line.pullback_reference_extreme
        resumed_without_retest = (
            broke_up and bar.close > reference + line.tolerance
        ) or (
            not broke_up and bar.close < reference - line.tolerance
        )
        if resumed_without_retest:
            line.state = TrendLineState.EXPIRED_WITHOUT_RETEST
            line.expired_index = index
            line.expired_time_ns = bar.ts_close_ns
            self._inc("line_retest_missed")
            return [
                self._event(
                    TrendLineEventKind.RETEST_MISSED,
                    line,
                    index,
                    bar,
                    level,
                ),
            ]

        # A wick made during the first pullback becomes part of the price the
        # market must later close through to prove that pullback has ended.
        line.pullback_reference_extreme = (
            max(reference, bar.high)
            if broke_up
            else min(reference, bar.low)
        )
        return []

    def _update_lines(self, index: int, bar: Candle) -> list[TrendLineEvent]:
        events: list[TrendLineEvent] = []
        broken_sides: set[TrendLineSide] = set()
        for line in self.lines:
            if index <= line.observed_index:
                continue
            level = line.price_at(index)
            touches = self._touches(bar, level, line.tolerance)

            if line.state is TrendLineState.ACTIVE:
                if touches:
                    closes_on_valid_side = (
                        bar.close >= level - line.tolerance
                        if line.side is TrendLineSide.SUPPORT
                        else bar.close <= level + line.tolerance
                    )
                    if closes_on_valid_side and (
                        line.last_touch_index is None
                        or index > line.last_touch_index + 1
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
                    bar.high
                    if line.side is TrendLineSide.RESISTANCE
                    else bar.low
                )
                line.continuation_extreme = line.break_extreme
                self._inc("line_break")
                events.append(
                    self._event(
                        TrendLineEventKind.BREAK,
                        line,
                        index,
                        bar,
                        level,
                    ),
                )
                broken_sides.add(line.side)
                continue

            if (
                line.state is TrendLineState.BROKEN
                and line.break_index is not None
                and index > line.break_index
            ):
                events.extend(
                    self._advance_broken_line(
                        line,
                        index,
                        bar,
                        level,
                        touches,
                    ),
                )

        # A break ends the anchor regime for new lines. A swing whose event
        # occurred before this close but is confirmed afterward may not seed the
        # post-break hull.
        for side in broken_sides:
            self._reset_hull(side, index)
        return events

    def on_bar(self, bar: Candle) -> list[TrendLineEvent]:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("bars must arrive in strictly increasing close time")
        self.bars.append(bar)
        created_swings = self.swing_tracker.on_bar(bar)
        index = len(self.bars) - 1

        # Existing lines see the close before newly confirmed, historically
        # located pivots are admitted into the post-close envelope.
        events = self._update_lines(index, bar)
        for swing in created_swings:
            events.extend(self._create_from_swing(swing))
        return events

    def line(self, line_id: str) -> CausalTrendLine | None:
        return next((line for line in self.lines if line.line_id == line_id), None)

    def active_lines(
        self,
        side: TrendLineSide | None = None,
    ) -> list[CausalTrendLine]:
        return [
            line
            for line in self.lines
            if line.state is TrendLineState.ACTIVE
            and (side is None or line.side is side)
        ]
