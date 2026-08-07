from __future__ import annotations

import unittest

from logic import Direction
from semantic_logic import (
    aac_boundary_prices,
    costed_market_economics,
    displacement_failure_stop,
    qualify_market_entry,
)


class Candidate14DevelopmentV2ExecutionTests(unittest.TestCase):
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

    def test_displacement_failure_stop_is_directionally_symmetric(self):
        short_stop = displacement_failure_stop(
            direction=Direction.SHORT,
            zone_low=100.0,
            zone_high=101.0,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        long_stop = displacement_failure_stop(
            direction=Direction.LONG,
            zone_low=100.0,
            zone_high=101.0,
            atr=2.0,
            stop_buffer_atr=0.08,
        )
        self.assertAlmostEqual(short_stop, 101.16)
        self.assertAlmostEqual(long_stop, 99.84)

    def test_market_economics_reserve_taker_entry_and_stop(self):
        risk, loss, gain, net_r = costed_market_economics(
            direction=Direction.SHORT,
            entry=2.0853,
            stop=2.0905,
            target=2.0559,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertAlmostEqual(risk, 0.0052)
        self.assertGreater(loss, risk)
        self.assertLess(gain, 2.0853 - 2.0559)
        self.assertGreater(net_r, 1.25)

    def test_costed_displacement_market_entry_can_qualify(self):
        qualified, risk, loss, gain, net_r = qualify_market_entry(
            direction=Direction.SHORT,
            entry=2.0853,
            stop=2.0905,
            target=2.0559,
            atr=0.02,
            min_stop_atr=0.08,
            min_net_r=1.25,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertTrue(qualified)
        self.assertGreater(risk / 0.02, 0.08)
        self.assertGreater(loss, risk)
        self.assertGreater(gain, 0.0)
        self.assertGreater(net_r, 1.25)

    def test_too_tight_displacement_stop_fails_execution_floor(self):
        qualified, *_ = qualify_market_entry(
            direction=Direction.LONG,
            entry=100.0,
            stop=99.95,
            target=105.0,
            atr=2.0,
            min_stop_atr=0.08,
            min_net_r=1.25,
            taker_rate=0.0008,
            target_maker_rate=0.0004,
        )
        self.assertFalse(qualified)

    def test_insufficient_after_cost_r_keeps_passive_fallback(self):
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

    def test_noncausal_market_order_is_rejected(self):
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
