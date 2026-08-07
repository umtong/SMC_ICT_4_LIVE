from __future__ import annotations

import unittest

from logic import Direction
from semantic_logic import aac_boundary_prices


class AcceptedAuctionExecutionTests(unittest.TestCase):
    def test_short_enters_defended_pivot_and_invalidates_above_source_low(self):
        entry, stop = aac_boundary_prices(
            direction=Direction.SHORT,
            defended_pullback=17.1120,
            source_boundary=17.2200,
            atr=0.0194,
            stop_buffer_atr=0.08,
        )
        self.assertEqual(entry, 17.1120)
        self.assertAlmostEqual(stop, 17.221552)
        self.assertGreater(stop, 17.2200)

    def test_long_is_directionally_symmetric(self):
        entry, stop = aac_boundary_prices(
            direction=Direction.LONG,
            defended_pullback=102.0,
            source_boundary=100.0,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        self.assertEqual(entry, 102.0)
        self.assertAlmostEqual(stop, 99.84)
        self.assertLess(stop, 100.0)


if __name__ == "__main__":
    unittest.main()
