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


if __name__ == "__main__":
    unittest.main()
