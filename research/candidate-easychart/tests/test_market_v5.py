from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle, Side
from market_v4 import ParallelChannel, StructuralPivot
from market_v5 import (
    DirectionalChangePivotDetector,
    EasyChartIntrinsicStructureEngine,
    ScenarioConfigV5,
)


NS = 60_000_000_000


def bar(index, open_, high, low, close, minutes=5):
    start = index * minutes * NS
    return Candle(start, start + minutes * NS - 1, open_, high, low, close, 1.0)


def horizontal_long_channel():
    p1 = StructuralPivot(0, 0, "HIGH", 110.0, 0, 0)
    p2 = StructuralPivot(1, 1, "LOW", 100.0, 1, 1)
    p3 = StructuralPivot(2, 2, "HIGH", 110.0, 2, 2)
    return ParallelChannel(
        channel_id="ch",
        observed_time_ns=2,
        timeframe_minutes=15,
        anchor_side="HIGH",
        expected_side=Side.LONG,
        base_time_ns=0,
        base_level=110.0,
        slope_per_ns=0.0,
        width=10.0,
        p1=p1,
        p2=p2,
        p3=p3,
    )


class TestDirectionalChangePivotDetector(unittest.TestCase):
    def test_pivots_are_volatility_adaptive_causal_and_alternating(self):
        detector = DirectionalChangePivotDetector(
            timeframe_minutes=5,
            atr_period=2,
            atr_multiple=1.0,
        )
        candles = [
            bar(0, 100.0, 101.0, 99.0, 100.0),
            bar(1, 100.0, 102.0, 100.0, 101.0),
            bar(2, 101.0, 101.2, 98.8, 99.5),
            bar(3, 99.5, 99.8, 97.0, 98.0),
            bar(4, 98.0, 101.0, 97.5, 100.5),
        ]
        events = []
        for index, candle in enumerate(candles):
            pivot = detector.on_candle(candle, index)
            if pivot is not None:
                events.append(pivot)
        self.assertEqual([event.side for event in events], ["HIGH", "LOW"])
        self.assertEqual(events[0].center_index, 1)
        self.assertEqual(events[0].observed_index, 2)
        self.assertEqual(events[0].level, 102.0)
        self.assertEqual(events[1].center_index, 3)
        self.assertEqual(events[1].observed_index, 4)
        self.assertLess(events[0].event_time_ns, events[0].observed_time_ns)

    def test_new_extreme_cannot_be_confirmed_by_same_candle(self):
        detector = DirectionalChangePivotDetector(
            timeframe_minutes=5,
            atr_period=2,
            atr_multiple=1.0,
        )
        seed = [
            bar(0, 100.0, 101.0, 99.0, 100.0),
            bar(1, 100.0, 102.0, 100.0, 101.0),
            bar(2, 101.0, 101.2, 98.8, 99.5),
            bar(3, 99.5, 99.8, 97.0, 98.0),
            bar(4, 98.0, 101.0, 97.5, 100.5),
        ]
        for index, candle in enumerate(seed):
            detector.on_candle(candle, index)
        # This bar prints a new high and closes sharply lower.  Its intrabar
        # ordering is unknown, so the new 105 high must not be confirmed now.
        same_bar = detector.on_candle(bar(5, 100.5, 105.0, 99.8, 100.0), 5)
        self.assertIsNone(same_bar)
        # The next fully observed bar closes far enough below the prior 105
        # extreme to confirm it using the pre-bar ATR threshold.
        later = detector.on_candle(bar(6, 100.0, 102.5, 99.5, 100.0), 6)
        self.assertIsNotNone(later)
        assert later is not None
        self.assertEqual(later.side, "HIGH")
        self.assertEqual(later.center_index, 5)
        self.assertEqual(later.observed_index, 6)


class TestDelayedTrapLifecycle(unittest.TestCase):
    def engine(self, **overrides):
        config = ScenarioConfigV5(
            min_body_ratio=1.0,
            min_previous_body_atr=0.0,
            enable_immediate_fakeout=False,
            enable_one_bar_trap=True,
            accepted_break_channel_widths=1.0,
            **overrides,
        )
        engine = EasyChartIntrinsicStructureEngine("BTCUSDT", config)
        engine.active_channel = horizontal_long_channel()
        engine.micro_high = StructuralPivot(0, 0, "HIGH", 103.0, 0, 1)
        return engine

    def test_trap_can_remain_outside_for_multiple_bars_before_reclaim(self):
        engine = self.engine()
        outside = [
            bar(1, 100.5, 100.7, 99.0, 99.5),
            bar(2, 99.5, 100.0, 98.0, 98.8),
            bar(3, 98.8, 99.7, 97.5, 99.2),
        ]
        for index, candle in enumerate(outside, start=1):
            engine._observe_channel_interaction(candle, index)
            self.assertIsNotNone(engine.outside)
            self.assertEqual(len(engine.episodes), 0)
        engine._observe_channel_interaction(bar(4, 99.2, 101.0, 99.0, 100.5), 4)
        self.assertIsNone(engine.outside)
        self.assertEqual(len(engine.episodes), 1)
        episode = engine.episodes[0]
        self.assertEqual(episode.family_prefix, "CHANNEL_POINT4_DELAYED_TRAP")
        self.assertEqual(episode.interaction_extreme, 97.5)

    def test_full_channel_width_continuation_is_accepted_break_not_trap(self):
        engine = self.engine()
        engine._observe_channel_interaction(bar(1, 100.5, 100.6, 99.0, 99.5), 1)
        self.assertIsNotNone(engine.outside)
        # Lower boundary is 100 and channel width is 10.  A close at 90 has
        # travelled one complete channel width outside the accepted range.
        engine._observe_channel_interaction(bar(2, 99.5, 99.8, 89.0, 90.0), 2)
        self.assertIsNone(engine.outside)
        self.assertIsNone(engine.active_channel)
        self.assertEqual(len(engine.episodes), 0)
        self.assertEqual(engine.diagnostics.get("channel_accepted_break_full_width"), 1)


if __name__ == "__main__":
    unittest.main()
