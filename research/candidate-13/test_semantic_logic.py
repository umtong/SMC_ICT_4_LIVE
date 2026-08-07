from __future__ import annotations

import unittest

from logic import Direction
from semantic_logic import aac_boundary_prices, costed_market_economics


class StructuralExecutionTests(unittest.TestCase):
    def test_short_aac_enters_pivot_and_invalidates_above_source_high(self):
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

    def test_long_aac_is_directionally_symmetric(self):
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

    def test_far_market_economics_include_taker_entry_and_stop(self):
        risk, loss, gain, net_r = costed_market_economics(
            direction=Direction.SHORT,
            entry=220.73,
            stop=223.16896533333332,
            target=211.27,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertAlmostEqual(risk, 2.4389653333333234)
        self.assertGreater(loss, risk)
        self.assertLess(gain, 220.73 - 211.27)
        self.assertGreater(net_r, 3.25)

    def test_low_after_cost_market_r_must_fall_back_to_passive(self):
        _, _, _, net_r = costed_market_economics(
            direction=Direction.SHORT,
            entry=0.4357,
            stop=0.4376368,
            target=0.4334,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertLess(net_r, 1.25)


if __name__ == "__main__":
    unittest.main()
