from __future__ import annotations

import math
import unittest

from domain import Candle
from easychart_zones import EasyChartZoneDetector, ZoneKind


class EasyChartZoneEdgeCaseTest(unittest.TestCase):
    def bar(self, index: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(index * 60_000_000_000, open_, high, low, close, 1.0)

    def test_adjacent_doji_does_not_create_infinite_fvg_strength(self) -> None:
        detector = EasyChartZoneDetector("BTCUSDT", 5, 0.1)
        detector.on_bar(self.bar(1, 100.0, 101.0, 99.0, 100.0))
        detector.on_bar(self.bar(2, 100.0, 106.0, 99.8, 105.0))
        created = detector.on_bar(self.bar(3, 103.0, 104.0, 102.0, 103.0))
        fvg = next(zone for zone in created if zone.kind is ZoneKind.FVG)
        self.assertTrue(math.isfinite(fvg.strength_ratio))
        self.assertGreaterEqual(fvg.strength_ratio, 2.0)
        self.assertAlmostEqual(fvg.strength_ratio, 50.0)

    def seed_ten_ranges(self, detector: EasyChartZoneDetector) -> None:
        for index in range(10):
            detector.on_bar(self.bar(index, 99.8, 100.5, 99.5, 100.2))

    def test_engulfed_source_doji_is_not_promoted_to_huge_order_block(self) -> None:
        detector = EasyChartZoneDetector("BTCUSDT", 5, 0.01)
        self.seed_ten_ranges(detector)
        # Body 0.05 versus prior ten-bar average range 1.0: TA-Lib's causal
        # BodyDoji threshold is 0.10. A large next candle must not turn this
        # pathological denominator into a high-quality OB.
        detector.on_bar(self.bar(10, 100.05, 100.5, 99.5, 100.00))
        created = detector.on_bar(self.bar(11, 99.90, 101.2, 99.7, 101.00))
        self.assertFalse(any(zone.kind is ZoneKind.ORDER_BLOCK for zone in created))
        self.assertEqual(detector.diagnostics.get("order_block_source_doji_rejected"), 1)

    def test_source_body_above_causal_doji_threshold_remains_eligible(self) -> None:
        detector = EasyChartZoneDetector("BTCUSDT", 5, 0.01)
        self.seed_ten_ranges(detector)
        detector.on_bar(self.bar(10, 100.11, 100.5, 99.5, 100.00))
        created = detector.on_bar(self.bar(11, 99.90, 101.2, 99.7, 101.00))
        order_block = next(zone for zone in created if zone.kind is ZoneKind.ORDER_BLOCK)
        self.assertGreater(order_block.source_body_to_average_range or 0.0, 0.10)
        self.assertGreaterEqual(order_block.strength_ratio, 2.0)


if __name__ == "__main__":
    unittest.main()
