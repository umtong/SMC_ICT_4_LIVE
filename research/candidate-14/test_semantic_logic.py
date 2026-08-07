from __future__ import annotations

import unittest

from logic import Direction
from semantic_logic import (
    aac_boundary_prices,
    costed_market_economics,
    qualify_market_entry,
)


class Candidate14StructuralExecutionTests(unittest.TestCase):
    def test_short_aac_invalidates_beyond_source_high(self):
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

    def test_market_economics_reserve_taker_entry_and_stop(self):
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

    def test_aac_confirmation_market_entry_qualifies_only_after_costs(self):
        qualified, risk, loss, gain, net_r = qualify_market_entry(
            direction=Direction.LONG,
            entry=105.0,
            stop=100.0,
            target=115.0,
            atr=5.0,
            min_stop_atr=0.08,
            min_net_r=1.25,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertTrue(qualified)
        self.assertEqual(risk, 5.0)
        self.assertGreater(loss, risk)
        self.assertLess(gain, 10.0)
        self.assertGreater(net_r, 1.25)

    def test_low_after_cost_market_r_falls_back_to_passive(self):
        qualified, _risk, _loss, _gain, net_r = qualify_market_entry(
            direction=Direction.LONG,
            entry=109.0,
            stop=100.0,
            target=110.0,
            atr=5.0,
            min_stop_atr=0.08,
            min_net_r=1.25,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertFalse(qualified)
        self.assertLess(net_r, 1.25)

    def test_noncausal_market_price_order_is_rejected(self):
        qualified, *_ = qualify_market_entry(
            direction=Direction.LONG,
            entry=99.0,
            stop=100.0,
            target=115.0,
            atr=5.0,
            min_stop_atr=0.08,
            min_net_r=1.25,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertFalse(qualified)


if __name__ == "__main__":
    unittest.main()
