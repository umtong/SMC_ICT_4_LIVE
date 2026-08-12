from __future__ import annotations

import unittest

from domain import Candle, Side
from market_structure import (
    MarketStructureDetector,
    PivotKind,
    StructureKind,
    StructurePath,
)

NS = 60_000_000_000


def bar(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle((i + 1) * NS, o, h, l, c, 1.0)


def ascending_seed() -> list[Candle]:
    return [
        bar(0, 12.0, 13.0, 11.8, 12.5),
        bar(1, 11.4, 12.2, 10.0, 11.5),  # first wick low
        bar(2, 13.0, 16.0, 12.0, 15.0),  # opposite wick high
        bar(3, 12.0, 14.0, 11.0, 13.0),  # second, higher wick low
        bar(4, 13.5, 15.0, 12.5, 14.0),  # confirms the second low
    ]


class MarketStructureV4Tests(unittest.TestCase):
    def test_pivot_is_not_visible_before_right_bar_closes(self) -> None:
        detector = MarketStructureDetector("TEST", 1, 0.1, pivot_spans=(1,))
        candles = [
            bar(0, 11.0, 12.0, 10.5, 11.5),
            bar(1, 10.7, 11.0, 9.0, 10.5),
            bar(2, 10.8, 12.0, 10.0, 11.5),
        ]
        detector.on_bar(candles[0])
        detector.on_bar(candles[1])
        self.assertEqual(detector.pivots, [])
        detector.on_bar(candles[2])
        lows = [item for item in detector.pivots if item.kind is PivotKind.LOW]
        self.assertEqual(len(lows), 1)
        self.assertEqual(lows[0].price, 9.0)
        self.assertEqual(lows[0].event_time_ns, candles[1].ts_close_ns)
        self.assertEqual(lows[0].observed_time_ns, candles[2].ts_close_ns)

    def test_channel_is_exactly_parallel_and_needs_three_confirmed_points(self) -> None:
        detector = MarketStructureDetector("TEST", 1, 0.1, pivot_spans=(1,))
        for candle in ascending_seed()[:-1]:
            detector.on_bar(candle)
        self.assertEqual(detector.channels, {})
        detector.on_bar(ascending_seed()[-1])
        self.assertEqual(len(detector.channels), 1)
        channel = next(iter(detector.channels.values()))
        lower = detector.find_boundary(channel.lower_boundary_id)
        upper = detector.find_boundary(channel.upper_boundary_id)
        self.assertIsNotNone(lower)
        self.assertIsNotNone(upper)
        assert lower is not None and upper is not None
        self.assertAlmostEqual(lower.slope_per_ns, upper.slope_per_ns)
        opposite_pivot = next(item for item in detector.pivots if item.kind is PivotKind.HIGH and item.index == 2)
        self.assertAlmostEqual(upper.level_at(opposite_pivot.event_time_ns), opposite_pivot.price)
        self.assertEqual(channel.observed_time_ns, ascending_seed()[-1].ts_close_ns)

    def test_first_later_channel_touch_is_fourth_point_and_targets_opposite_edge(self) -> None:
        detector = MarketStructureDetector("TEST", 1, 0.1, pivot_spans=(1,))
        seed = ascending_seed()
        events = []
        for candle in seed:
            events.extend(detector.on_bar(candle))
        self.assertEqual(events, [])
        # Lower channel line is 12.0 at this close. Touch and close back inside.
        events = detector.on_bar(bar(5, 13.0, 14.0, 12.0, 13.2))
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.path, StructurePath.BOUNCE)
        self.assertEqual(event.side, Side.LONG)
        self.assertEqual(event.structure_kind, StructureKind.CHANNEL_LOWER)
        self.assertEqual(event.interaction_index, 5)
        target = detector.find_boundary(event.target_boundary_id or "")
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.kind, StructureKind.CHANNEL_UPPER)
        self.assertAlmostEqual(event.target_price_at_interaction or 0.0, target.level_at(event.interaction_time_ns))
        channel = detector.channels[event.channel_id or ""]
        self.assertEqual(channel.first_interaction_time_ns, event.interaction_time_ns)

    def test_lower_bar_cannot_resolve_higher_timeframe_fakeout(self) -> None:
        detector = MarketStructureDetector("TEST", 1, 0.1, pivot_spans=(1,))
        for candle in ascending_seed():
            detector.on_bar(candle)
        lower = Candle(
            ts_close_ns=ascending_seed()[-1].ts_close_ns + NS // 5,
            open=13.0,
            high=14.0,
            low=11.4,
            close=12.8,
            volume=1.0,
        )
        self.assertEqual(detector.observe_lower_bar(lower), [])
        self.assertGreater(
            detector.diagnostics.get("lower_support_provisional_reclaim_observed", 0),
            0,
        )
        # The context-timeframe close still resolves the same geometry.
        events = detector.on_bar(bar(5, 13.0, 14.0, 11.4, 12.8))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].path, StructurePath.FAKEOUT)

    def test_wick_outside_close_inside_is_immediate_fakeout(self) -> None:
        detector = MarketStructureDetector("TEST", 1, 0.1, pivot_spans=(1,))
        for candle in ascending_seed():
            detector.on_bar(candle)
        events = detector.on_bar(bar(5, 13.0, 14.0, 11.4, 12.8))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].path, StructurePath.FAKEOUT)
        self.assertEqual(events[0].side, Side.LONG)
        self.assertLess(events[0].stop_reference, events[0].interaction_extreme)

    def test_body_break_is_not_accepted_until_next_bar_holds_outside(self) -> None:
        detector = MarketStructureDetector("TEST", 1, 0.1, pivot_spans=(1,))
        for candle in ascending_seed():
            detector.on_bar(candle)
        first = detector.on_bar(bar(5, 13.0, 13.2, 11.0, 11.5))
        self.assertEqual(first, [])
        second = detector.on_bar(bar(6, 11.6, 12.0, 10.8, 11.7))
        accepted = [event for event in second if event.side is Side.SHORT]
        self.assertTrue(accepted)
        self.assertIn(
            accepted[0].path,
            {StructurePath.ACCEPTANCE, StructurePath.CHANNEL_FAILURE_ACCEPTANCE},
        )
        self.assertIsNotNone(accepted[0].origin_price)
        self.assertGreater(accepted[0].stop_reference, accepted[0].reference_close)

    def test_outside_break_then_return_inside_is_trap_reentry(self) -> None:
        detector = MarketStructureDetector("TEST", 1, 0.1, pivot_spans=(1,))
        for candle in ascending_seed():
            detector.on_bar(candle)
        self.assertEqual(detector.on_bar(bar(5, 13.0, 13.2, 11.0, 11.5)), [])
        events = detector.on_bar(bar(6, 11.6, 13.5, 11.2, 13.0))
        long_events = [event for event in events if event.side is Side.LONG]
        self.assertTrue(long_events)
        self.assertEqual(long_events[0].path, StructurePath.TRAP_REENTRY)


if __name__ == "__main__":
    unittest.main()
