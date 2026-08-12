from __future__ import annotations

import unittest

from domain import Candle, Side
from market_structure import StructureKind, StructurePath
from market_structure_horizontal_range_v4 import (
    HorizontalRangeDirection,
    HorizontalRangeMarketStructureDetector,
)

NS = 60_000_000_000


def bar(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle((i + 1) * NS, o, h, l, c, 1.0)


def overlapping_box_seed() -> list[Candle]:
    return [
        bar(0, 12.0, 12.5, 11.5, 12.1),
        bar(1, 10.5, 11.2, 10.0, 10.4),
        bar(2, 12.0, 14.0, 11.0, 13.5),
        bar(3, 10.4, 11.3, 10.0, 10.3),
        bar(4, 12.0, 12.5, 11.0, 12.1),
    ]


class HorizontalRangeTests(unittest.TestCase):
    def _detector(self) -> HorizontalRangeMarketStructureDetector:
        return HorizontalRangeMarketStructureDetector(
            "TEST",
            1,
            0.1,
            pivot_spans=(1,),
        )

    def test_range_is_not_visible_before_third_pivot_is_confirmed(self) -> None:
        detector = self._detector()
        seed = overlapping_box_seed()
        for candle in seed[:-1]:
            detector.on_bar(candle)
        self.assertFalse(detector.horizontal_range_ids)
        detector.on_bar(seed[-1])
        self.assertEqual(len(detector.horizontal_range_ids), 1)

    def test_overlapping_wick_rejections_build_exact_horizontal_three_point_box(self) -> None:
        detector = self._detector()
        for candle in overlapping_box_seed():
            detector.on_bar(candle)
        self.assertEqual(len(detector.horizontal_range_ids), 1)
        channel_id = next(iter(detector.horizontal_range_ids))
        channel = detector.channels[channel_id]
        self.assertEqual(channel.direction, HorizontalRangeDirection.HORIZONTAL)
        self.assertEqual(channel.anchor_pivot_ids.__len__(), 3)
        lower = detector.find_boundary(channel.lower_boundary_id)
        upper = detector.find_boundary(channel.upper_boundary_id)
        self.assertIsNotNone(lower)
        self.assertIsNotNone(upper)
        assert lower is not None and upper is not None
        self.assertEqual(lower.kind, StructureKind.CHANNEL_LOWER)
        self.assertEqual(upper.kind, StructureKind.CHANNEL_UPPER)
        self.assertEqual(lower.slope_per_ns, 0.0)
        self.assertEqual(upper.slope_per_ns, 0.0)
        self.assertAlmostEqual(lower.anchor_1_price, 10.0)
        self.assertAlmostEqual(upper.anchor_1_price, 14.0)
        self.assertEqual(channel.observed_index, 4)

    def test_first_later_interaction_is_fourth_point_and_targets_other_edge(self) -> None:
        detector = self._detector()
        for candle in overlapping_box_seed():
            # Existing swing boundaries may legitimately emit earlier events;
            # this assertion is about the newly formed horizontal range only.
            detector.on_bar(candle)
        events = detector.on_bar(bar(5, 13.7, 14.0, 12.8, 13.6))
        horizontal = [
            event
            for event in events
            if detector.is_horizontal_range(event.channel_id)
        ]
        self.assertEqual(len(horizontal), 1)
        event = horizontal[0]
        self.assertEqual(event.path, StructurePath.BOUNCE)
        self.assertEqual(event.side, Side.SHORT)
        self.assertEqual(event.interaction_index, 5)
        target = detector.find_boundary(event.target_boundary_id or "")
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.kind, StructureKind.CHANNEL_LOWER)
        self.assertAlmostEqual(event.target_price_at_interaction or 0.0, 10.0)

    def test_disjoint_wick_rejection_areas_do_not_create_box(self) -> None:
        detector = self._detector()
        candles = [
            bar(0, 12.0, 12.5, 11.5, 12.1),
            bar(1, 10.3, 11.2, 10.0, 10.2),  # rejection band [10.0, 10.2]
            bar(2, 12.0, 14.0, 11.0, 13.5),
            bar(3, 10.8, 11.3, 10.5, 10.7),  # rejection band [10.5, 10.7]
            bar(4, 12.0, 12.5, 11.0, 12.1),
        ]
        for candle in candles:
            detector.on_bar(candle)
        self.assertFalse(detector.horizontal_range_ids)
        self.assertGreater(
            detector.diagnostics.get(
                "horizontal_range_same_side_rejection_bands_disjoint",
                0,
            ),
            0,
        )

    def test_wick_sweep_beyond_range_edge_closing_inside_is_fakeout(self) -> None:
        detector = self._detector()
        for candle in overlapping_box_seed():
            detector.on_bar(candle)
        events = detector.on_bar(bar(5, 13.7, 14.4, 12.8, 13.8))
        horizontal = [
            event
            for event in events
            if detector.is_horizontal_range(event.channel_id)
        ]
        self.assertEqual(len(horizontal), 1)
        self.assertEqual(horizontal[0].path, StructurePath.FAKEOUT)
        self.assertEqual(horizontal[0].side, Side.SHORT)
        self.assertGreater(horizontal[0].stop_reference, 14.4)


if __name__ == "__main__":
    unittest.main()
