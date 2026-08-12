from __future__ import annotations

import unittest

from domain import Candle
from easychart_zones import ZoneSide
from horizontal_structure_v3 import HorizontalStructureDetector


class HorizontalStructureDetectorTest(unittest.TestCase):
    NS = 60_000_000_000

    def bar(self, minute: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(minute * self.NS, open_, high, low, close, 1.0)

    def test_two_confirmed_overlapping_wick_rejections_form_support_without_atr_tolerance(self) -> None:
        detector = HorizontalStructureDetector("BTCUSDT", 15, 0.1)
        bars = [
            self.bar(1, 103.0, 104.0, 102.0, 103.5),
            self.bar(2, 102.5, 103.0, 101.5, 102.0),
            self.bar(3, 101.2, 102.0, 100.0, 101.0),  # first support pivot; wick [100, 101]
            self.bar(4, 101.3, 102.4, 100.8, 102.0),
            self.bar(5, 102.0, 103.0, 101.2, 102.5),  # first pivot becomes observable
            self.bar(6, 102.3, 102.8, 101.1, 101.8),
            self.bar(7, 101.4, 102.1, 100.5, 101.2),  # second wick [100.5, 101.2]
            self.bar(8, 101.5, 102.5, 101.0, 102.0),
            self.bar(9, 102.0, 103.0, 101.3, 102.6),  # second pivot confirmed
        ]
        created = []
        for bar in bars:
            created.extend(detector.on_bar(bar))
        supports = [zone for zone in created if zone.side is ZoneSide.SUPPORT]
        self.assertEqual(len(supports), 1)
        structure = supports[0]
        self.assertAlmostEqual(structure.lower, 100.5)
        self.assertAlmostEqual(structure.upper, 101.0)
        self.assertEqual(structure.formation_indices, (2, 6))
        self.assertEqual(structure.observed_time_ns, bars[-1].ts_close_ns)

    def test_touch_of_shared_band_is_not_a_sweep_but_trade_beyond_far_edge_is(self) -> None:
        detector = HorizontalStructureDetector("BTCUSDT", 15, 0.1)
        # Directly reuse the causal construction from the first test.
        bars = [
            self.bar(1, 103.0, 104.0, 102.0, 103.5),
            self.bar(2, 102.5, 103.0, 101.5, 102.0),
            self.bar(3, 101.2, 102.0, 100.0, 101.0),
            self.bar(4, 101.3, 102.4, 100.8, 102.0),
            self.bar(5, 102.0, 103.0, 101.2, 102.5),
            self.bar(6, 102.3, 102.8, 101.1, 101.8),
            self.bar(7, 101.4, 102.1, 100.5, 101.2),
            self.bar(8, 101.5, 102.5, 101.0, 102.0),
            self.bar(9, 102.0, 103.0, 101.3, 102.6),
        ]
        for bar in bars:
            detector.on_bar(bar)
        structure = detector.active_zones(side=ZoneSide.SUPPORT)[0]
        detector.observe_price(self.bar(10, 102.0, 102.3, 100.5, 101.4))
        self.assertTrue(structure.active)
        detector.observe_price(self.bar(11, 101.4, 101.7, 100.4, 101.2))
        self.assertFalse(structure.active)
        self.assertEqual(structure.consumed_time_ns, 11 * self.NS)


if __name__ == "__main__":
    unittest.main()
