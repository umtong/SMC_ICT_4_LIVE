from __future__ import annotations

import unittest

from causal_trendlines import (
    CausalTrendLineTracker,
    TrendLineEventKind,
    TrendLineSide,
    TrendLineState,
)
from domain import Candle


class CausalTrendLineTrackerTest(unittest.TestCase):
    def bar(
        self,
        index: int,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> Candle:
        return Candle(index * 60_000_000_000, open_, high, low, close, 1.0)

    def resistance_fixture(self) -> list[Candle]:
        return [
            self.bar(0, 9.5, 10.0, 9.0, 9.5),
            self.bar(1, 11.0, 12.0, 10.5, 11.5),
            self.bar(2, 10.5, 11.0, 10.0, 10.5),
            self.bar(3, 10.0, 10.5, 9.8, 10.1),
            self.bar(4, 10.7, 11.0, 10.3, 10.8),
            self.bar(5, 10.2, 10.5, 9.9, 10.1),
            self.bar(6, 10.0, 10.2, 9.8, 10.0),
        ]

    def test_resistance_is_observable_only_after_second_pivot_confirmation(self) -> None:
        tracker = CausalTrendLineTracker(
            "BTCUSDT",
            5,
            0.1,
            swing_span=1,
            min_anchor_bars=3,
            tolerance_range_fraction=0.0,
        )
        fixture = self.resistance_fixture()
        for bar in fixture[:5]:
            events = tracker.on_bar(bar)
            self.assertFalse(any(event.kind is TrendLineEventKind.CREATED for event in events))
        events = tracker.on_bar(fixture[5])
        created = [event for event in events if event.kind is TrendLineEventKind.CREATED]
        self.assertEqual(len(created), 1)
        line = tracker.lines[0]
        self.assertIs(line.side, TrendLineSide.RESISTANCE)
        self.assertEqual(line.first_index, 1)
        self.assertEqual(line.second_index, 4)
        self.assertEqual(line.observed_index, 5)
        self.assertEqual(line.observed_time_ns, fixture[5].ts_close_ns)
        self.assertAlmostEqual(line.slope_per_bar, -1.0 / 3.0)

    def test_directional_break_then_first_later_retest(self) -> None:
        tracker = CausalTrendLineTracker(
            "BTCUSDT",
            5,
            0.1,
            swing_span=1,
            min_anchor_bars=3,
            tolerance_range_fraction=0.0,
        )
        for bar in self.resistance_fixture():
            tracker.on_bar(bar)

        break_bar = self.bar(7, 10.0, 10.5, 9.9, 10.45)
        break_events = tracker.on_bar(break_bar)
        self.assertEqual(
            [event.kind for event in break_events if event.kind is TrendLineEventKind.BREAK],
            [TrendLineEventKind.BREAK],
        )
        line = tracker.lines[0]
        self.assertIs(line.state, TrendLineState.BROKEN)
        self.assertEqual(line.break_index, 7)

        retest_bar = self.bar(8, 10.2, 10.3, 9.6, 10.05)
        retest_events = tracker.on_bar(retest_bar)
        self.assertEqual(
            [
                event.kind
                for event in retest_events
                if event.kind is TrendLineEventKind.FIRST_RETEST
            ],
            [TrendLineEventKind.FIRST_RETEST],
        )
        self.assertIs(line.state, TrendLineState.RETESTED)
        self.assertEqual(line.retest_index, 8)
        self.assertAlmostEqual(line.retest_level or 0.0, 9.666666666666666)

    def test_close_back_through_line_is_failed_break_not_retest(self) -> None:
        tracker = CausalTrendLineTracker(
            "BTCUSDT",
            5,
            0.1,
            swing_span=1,
            min_anchor_bars=3,
            tolerance_range_fraction=0.0,
        )
        for bar in self.resistance_fixture():
            tracker.on_bar(bar)
        tracker.on_bar(self.bar(7, 10.0, 10.5, 9.9, 10.45))
        events = tracker.on_bar(self.bar(8, 9.7, 9.8, 9.2, 9.4))
        self.assertEqual(
            [event.kind for event in events if event.kind is TrendLineEventKind.FAILED_BREAK],
            [TrendLineEventKind.FAILED_BREAK],
        )
        self.assertIs(tracker.lines[0].state, TrendLineState.FAILED_BREAK)

    def test_line_already_closed_through_before_observation_is_rejected(self) -> None:
        tracker = CausalTrendLineTracker(
            "BTCUSDT",
            5,
            0.1,
            swing_span=1,
            min_anchor_bars=3,
            tolerance_range_fraction=0.0,
        )
        bars = [
            self.bar(0, 9.5, 10.0, 9.0, 9.5),
            self.bar(1, 8.5, 9.0, 8.0, 8.4),
            self.bar(2, 9.0, 9.5, 8.6, 9.1),
            self.bar(3, 9.5, 10.0, 9.2, 9.6),
            self.bar(4, 9.4, 9.8, 9.0, 9.5),
            # Confirms the second swing low at index 4, but the close is already
            # below the projected support line as it becomes observable.
            self.bar(5, 9.2, 9.6, 9.05, 9.1),
        ]
        events: list[object] = []
        for bar in bars:
            events.extend(tracker.on_bar(bar))
        self.assertEqual(tracker.lines, [])
        self.assertEqual(tracker.diagnostics.get("candidate_broken_before_observable"), 1)
        self.assertFalse(
            any(
                getattr(event, "kind", None) is TrendLineEventKind.CREATED
                for event in events
            ),
        )


if __name__ == "__main__":
    unittest.main()
