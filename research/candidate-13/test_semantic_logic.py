from __future__ import annotations

import unittest

from logic import Direction
from semantic_logic import aac_equilibrium_prices


class AcceptedAuctionExecutionTests(unittest.TestCase):
    def test_short_uses_equilibrium_and_invalidates_beyond_defended_high(self):
        entry, stop = aac_equilibrium_prices(
            direction=Direction.SHORT,
            void_entry=17.0414,
            defended_pullback=17.1120,
            atr=0.0194,
            acceptance_retest_atr=0.18,
            stop_buffer_atr=0.08,
        )
        self.assertAlmostEqual(entry, 17.0767)
        self.assertAlmostEqual(stop, 17.117044)
        self.assertGreater(stop, 17.1120)

    def test_long_is_directionally_symmetric(self):
        entry, stop = aac_equilibrium_prices(
            direction=Direction.LONG,
            void_entry=100.0,
            defended_pullback=98.0,
            atr=2.0,
            acceptance_retest_atr=0.18,
            stop_buffer_atr=0.08,
        )
        self.assertEqual(entry, 99.0)
        self.assertAlmostEqual(stop, 97.48)
        self.assertLess(stop, 98.0)


if __name__ == "__main__":
    unittest.main()
