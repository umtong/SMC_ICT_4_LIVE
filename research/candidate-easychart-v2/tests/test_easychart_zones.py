from __future__ import annotations

import unittest

from domain import Candle
from easychart_zones import EasyChartZoneDetector, ZoneKind, ZoneSide, overlap_zones


class EasyChartZoneDetectorTest(unittest.TestCase):
    def bar(self, index: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(index * 60_000_000_000, open_, high, low, close, 1.0)

    def test_bullish_order_block_is_engulfed_bearish_body_not_engulfing_body(self) -> None:
        detector = EasyChartZoneDetector("BTCUSDT", 15, 0.1)
        detector.on_bar(self.bar(1, 100.0, 101.0, 97.0, 98.0))
        created = detector.on_bar(self.bar(2, 97.0, 103.0, 96.0, 102.0))
        order_blocks = [zone for zone in created if zone.kind is ZoneKind.ORDER_BLOCK]
        self.assertEqual(len(order_blocks), 1)
        zone = order_blocks[0]
        self.assertIs(zone.side, ZoneSide.SUPPORT)
        self.assertEqual((zone.lower, zone.upper), (98.0, 100.0))
        self.assertEqual(zone.invalidation, 95.9)
        self.assertEqual(zone.formed_index, 1)
        self.assertEqual(zone.observed_time_ns, self.bar(2, 97.0, 103.0, 96.0, 102.0).ts_close_ns)
        self.assertTrue(zone.high_quality_by_size)

    def test_bearish_order_block_is_engulfed_bullish_body(self) -> None:
        detector = EasyChartZoneDetector("ETHUSDT", 60, 0.01)
        detector.on_bar(self.bar(1, 100.0, 103.0, 99.0, 102.0))
        created = detector.on_bar(self.bar(2, 103.0, 104.0, 96.0, 97.0))
        zone = next(zone for zone in created if zone.kind is ZoneKind.ORDER_BLOCK)
        self.assertIs(zone.side, ZoneSide.RESISTANCE)
        self.assertEqual((zone.lower, zone.upper), (100.0, 102.0))
        self.assertAlmostEqual(zone.invalidation, 104.01)

    def test_order_block_formation_candle_does_not_mitigate_itself(self) -> None:
        detector = EasyChartZoneDetector("BTCUSDT", 15, 0.1)
        detector.on_bar(self.bar(1, 100.0, 101.0, 97.0, 98.0))
        zone = next(
            zone
            for zone in detector.on_bar(self.bar(2, 97.0, 103.0, 96.0, 102.0))
            if zone.kind is ZoneKind.ORDER_BLOCK
        )
        self.assertIsNone(zone.first_touch_index)
        detector.on_bar(self.bar(3, 102.0, 104.0, 99.0, 103.0))
        self.assertEqual(zone.first_touch_index, 2)

    def test_bullish_fvg_requires_wick_gap_and_two_x_middle_body(self) -> None:
        detector = EasyChartZoneDetector("BTCUSDT", 15, 0.1)
        detector.on_bar(self.bar(1, 100.0, 101.0, 99.0, 100.5))
        detector.on_bar(self.bar(2, 100.4, 106.0, 100.2, 105.4))
        created = detector.on_bar(self.bar(3, 104.8, 107.0, 103.0, 105.5))
        zone = next(zone for zone in created if zone.kind is ZoneKind.FVG)
        self.assertIs(zone.side, ZoneSide.SUPPORT)
        self.assertEqual((zone.lower, zone.upper), (101.0, 103.0))
        self.assertGreaterEqual(zone.strength_ratio, 2.0)

    def test_three_similar_candles_are_not_fvg_even_with_small_gap(self) -> None:
        detector = EasyChartZoneDetector("BTCUSDT", 15, 0.1)
        detector.on_bar(self.bar(1, 100.0, 101.0, 99.0, 100.5))
        detector.on_bar(self.bar(2, 100.5, 102.0, 100.4, 101.5))
        created = detector.on_bar(self.bar(3, 101.8, 103.0, 101.2, 102.5))
        self.assertFalse(any(zone.kind is ZoneKind.FVG for zone in created))
        self.assertEqual(detector.diagnostics.get("fvg_middle_body_below_two_x"), 1)

    def test_same_side_cross_timeframe_zones_return_price_intersection(self) -> None:
        higher_detector = EasyChartZoneDetector("BTCUSDT", 60, 0.1)
        higher_detector.on_bar(self.bar(1, 100.0, 101.0, 97.0, 98.0))
        higher = next(
            zone
            for zone in higher_detector.on_bar(self.bar(2, 97.0, 103.0, 96.0, 102.0))
            if zone.kind is ZoneKind.ORDER_BLOCK
        )
        lower_detector = EasyChartZoneDetector("BTCUSDT", 15, 0.1)
        lower_detector.on_bar(self.bar(3, 99.5, 100.5, 98.0, 98.5))
        lower = next(
            zone
            for zone in lower_detector.on_bar(self.bar(4, 98.0, 102.0, 97.0, 101.0))
            if zone.kind is ZoneKind.ORDER_BLOCK
        )
        overlap = overlap_zones(higher, lower)
        self.assertIsNotNone(overlap)
        assert overlap is not None
        self.assertEqual((overlap.lower, overlap.upper), (98.5, 99.5))
        self.assertEqual(overlap.higher_timeframe_minutes, 60)
        self.assertEqual(overlap.lower_timeframe_minutes, 15)

    def test_opposite_side_zones_do_not_form_confluence(self) -> None:
        support_detector = EasyChartZoneDetector("BTCUSDT", 60, 0.1)
        support_detector.on_bar(self.bar(1, 100.0, 101.0, 97.0, 98.0))
        support = next(
            zone
            for zone in support_detector.on_bar(self.bar(2, 97.0, 103.0, 96.0, 102.0))
            if zone.kind is ZoneKind.ORDER_BLOCK
        )
        resistance_detector = EasyChartZoneDetector("BTCUSDT", 15, 0.1)
        resistance_detector.on_bar(self.bar(3, 98.0, 101.0, 97.0, 100.0))
        resistance = next(
            zone
            for zone in resistance_detector.on_bar(self.bar(4, 101.0, 102.0, 96.0, 97.0))
            if zone.kind is ZoneKind.ORDER_BLOCK
        )
        self.assertIsNone(overlap_zones(support, resistance))

    def test_zone_invalidated_before_later_touch_is_not_active(self) -> None:
        detector = EasyChartZoneDetector("BTCUSDT", 15, 0.1)
        detector.on_bar(self.bar(1, 100.0, 101.0, 97.0, 98.0))
        zone = next(
            zone
            for zone in detector.on_bar(self.bar(2, 97.0, 103.0, 96.0, 102.0))
            if zone.kind is ZoneKind.ORDER_BLOCK
        )
        detector.on_bar(self.bar(3, 102.0, 103.0, 95.8, 96.5))
        self.assertFalse(zone.active)
        self.assertEqual(zone.invalidated_index, 2)


if __name__ == "__main__":
    unittest.main()
