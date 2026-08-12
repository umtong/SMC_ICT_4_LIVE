from __future__ import annotations

import unittest

from causal_swings import SwingSide
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

    def tracker(self) -> CausalTrendLineTracker:
        return CausalTrendLineTracker(
            "BTCUSDT",
            5,
            0.1,
            swing_span=1,
            min_anchor_bars=3,
            tolerance_range_fraction=0.0,
        )

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
        tracker = self.tracker()
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
        tracker = self.tracker()
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
        tracker = self.tracker()
        for bar in self.resistance_fixture():
            tracker.on_bar(bar)
        tracker.on_bar(self.bar(7, 10.0, 10.5, 9.9, 10.45))
        events = tracker.on_bar(self.bar(8, 9.7, 9.8, 9.2, 9.4))
        self.assertEqual(
            [event.kind for event in events if event.kind is TrendLineEventKind.FAILED_BREAK],
            [TrendLineEventKind.FAILED_BREAK],
        )
        self.assertIs(tracker.lines[0].state, TrendLineState.FAILED_BREAK)

    def test_line_already_wicked_through_before_observation_is_rejected(self) -> None:
        tracker = self.tracker()
        fixture = self.resistance_fixture()
        fixture[2] = self.bar(2, 10.5, 11.9, 10.0, 10.5)
        for bar in fixture[:6]:
            tracker.on_bar(bar)
        self.assertEqual(tracker.lines, [])
        self.assertEqual(tracker.diagnostics.get("candidate_wick_cross_before_observable"), 1)
        self.assertEqual(tracker.diagnostics.get("candidate_broken_before_observable"), 1)

    def test_line_already_closed_through_before_observation_is_rejected(self) -> None:
        tracker = self.tracker()
        bars = [
            self.bar(0, 9.5, 10.0, 9.0, 9.5),
            self.bar(1, 8.5, 9.0, 8.0, 8.4),
            self.bar(2, 9.0, 9.5, 8.6, 9.1),
            self.bar(3, 9.5, 10.0, 9.2, 9.6),
            self.bar(4, 9.4, 9.8, 9.0, 9.5),
            # Confirms the second swing low at index 4, but the close and wick
            # are already below the projected support as it becomes observable.
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

    def test_upper_hull_avoids_exhaustive_pair_explosion(self) -> None:
        tracker = self.tracker()
        values = [
            (10.0, 10.5, 9.5, 10.0),
            (13.0, 14.0, 12.5, 13.5),
            (11.5, 12.0, 11.0, 11.5),
            (10.5, 10.8, 10.0, 10.4),
            (10.5, 11.0, 10.0, 10.5),
            (9.7, 10.0, 9.5, 9.8),
            (10.5, 11.0, 10.0, 10.5),
            (11.5, 12.0, 11.0, 11.5),
            (10.0, 10.5, 9.5, 10.0),
            (9.5, 9.8, 9.0, 9.4),
            (9.5, 10.0, 9.0, 9.5),
            (8.8, 9.0, 8.5, 8.8),
        ]
        for index, value in enumerate(values):
            tracker.on_bar(self.bar(index, *value))
        edges = {(line.first_index, line.second_index) for line in tracker.lines}
        self.assertEqual(edges, {(1, 4), (1, 7), (7, 10)})
        self.assertNotIn((4, 10), edges)
        self.assertEqual(tracker.diagnostics.get("upper_hull_popped"), 1)

    def test_active_line_absorbs_collinear_third_touch(self) -> None:
        tracker = self.tracker()
        for bar in self.resistance_fixture():
            tracker.on_bar(bar)
        tracker.on_bar(self.bar(7, 9.2, 9.4, 9.0, 9.2))
        # The projected resistance at index 8 is 9.6666...
        tracker.on_bar(self.bar(8, 9.4, 9.6666666667, 9.1, 9.4))
        tracker.on_bar(self.bar(9, 9.2, 9.4, 9.0, 9.2))
        self.assertEqual(len(tracker.lines), 1)
        self.assertGreaterEqual(tracker.lines[0].touch_count, 3)
        self.assertEqual(tracker.diagnostics.get("active_line_represented_new_swing"), 1)

    def test_break_resets_anchor_hull(self) -> None:
        tracker = self.tracker()
        for bar in self.resistance_fixture():
            tracker.on_bar(bar)
        tracker.on_bar(self.bar(7, 10.0, 10.5, 9.9, 10.45))
        self.assertEqual(tracker.hulls[SwingSide.HIGH], [])
        self.assertEqual(tracker.hull_reset_index[SwingSide.HIGH], 7)
        self.assertEqual(tracker.diagnostics.get("hull_reset_after_break"), 1)

    def test_first_pullback_missed_expires_later_retest(self) -> None:
        tracker = self.tracker()
        for bar in self.resistance_fixture():
            tracker.on_bar(bar)
        tracker.on_bar(self.bar(7, 10.0, 10.5, 9.9, 10.45))

        # First pullback starts but remains well above the projected line.
        events = tracker.on_bar(self.bar(8, 10.6, 10.7, 10.2, 10.3))
        self.assertFalse(
            any(event.kind is TrendLineEventKind.FIRST_RETEST for event in events),
        )
        # The breakout resumes through the pre-pullback high. The first pullback
        # is now complete, so a much later line contact is not the first retest.
        events = tracker.on_bar(self.bar(9, 10.3, 11.1, 10.2, 10.9))
        self.assertTrue(
            any(event.kind is TrendLineEventKind.RETEST_MISSED for event in events),
        )
        self.assertIs(
            tracker.lines[0].state,
            TrendLineState.EXPIRED_WITHOUT_RETEST,
        )
        later = tracker.on_bar(self.bar(10, 10.8, 10.9, 8.9, 9.5))
        self.assertFalse(
            any(event.kind is TrendLineEventKind.FIRST_RETEST for event in later),
        )


if __name__ == "__main__":
    unittest.main()
