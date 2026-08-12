from __future__ import annotations

import unittest

from causal_swings import CausalSwingTracker, SwingSide
from domain import Candle


class CausalSwingTrackerTest(unittest.TestCase):
    def bar(self, index: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(index * 300_000_000_000, open_, high, low, close, 1.0)

    def test_swing_is_not_observable_until_right_window_closes(self) -> None:
        tracker = CausalSwingTracker("BTCUSDT", 5, span=2)
        bars = [
            self.bar(1, 10.0, 11.0, 9.0, 10.0),
            self.bar(2, 10.0, 10.5, 8.5, 9.0),
            self.bar(3, 9.0, 10.0, 7.0, 8.0),
            self.bar(4, 8.0, 10.5, 8.0, 9.5),
            self.bar(5, 9.5, 11.0, 8.5, 10.0),
        ]
        for bar in bars[:4]:
            tracker.on_bar(bar)
        self.assertFalse(any(swing.level == 7.0 for swing in tracker.swings))
        tracker.on_bar(bars[4])
        swing = next(swing for swing in tracker.swings if swing.level == 7.0)
        self.assertIs(swing.side, SwingSide.LOW)
        self.assertEqual(swing.event_time_ns, bars[2].ts_close_ns)
        self.assertEqual(swing.observed_time_ns, bars[4].ts_close_ns)

    def test_support_overlap_uses_latest_observable_low_near_zone(self) -> None:
        tracker = CausalSwingTracker("BTCUSDT", 5, span=2)
        for bar in [
            self.bar(1, 101.0, 102.0, 100.5, 101.0),
            self.bar(2, 101.0, 101.5, 99.8, 100.0),
            self.bar(3, 100.0, 101.0, 99.2, 100.5),
            self.bar(4, 100.5, 101.5, 99.7, 101.0),
            self.bar(5, 101.0, 102.0, 100.0, 101.5),
        ]:
            tracker.on_bar(bar)
        swing = tracker.strongest_eligible(
            side=SwingSide.LOW,
            overlap_lower=99.0,
            overlap_upper=100.0,
            before_ns=self.bar(6, 0, 0, 0, 0).ts_close_ns,
        )
        self.assertIsNotNone(swing)
        assert swing is not None
        self.assertEqual(swing.level, 99.2)

    def test_far_away_low_is_not_relabelled_as_zone_liquidity(self) -> None:
        tracker = CausalSwingTracker("BTCUSDT", 5, span=2)
        tracker.swings.append(
            __import__("causal_swings").SwingPoint(
                swing_id="far",
                side=SwingSide.LOW,
                level=95.0,
                event_index=0,
                observed_index=2,
                event_time_ns=1,
                observed_time_ns=2,
                span=2,
            ),
        )
        swing = tracker.strongest_eligible(
            side=SwingSide.LOW,
            overlap_lower=99.0,
            overlap_upper=100.0,
            before_ns=10,
        )
        self.assertIsNone(swing)


if __name__ == "__main__":
    unittest.main()
