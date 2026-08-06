from __future__ import annotations

import math
import unittest

from nt_liquidity_strategy import auction_efficiency
from nt_liquidity_strategy import cost_aware_target
from nt_liquidity_strategy import floor_quantity
from nt_liquidity_strategy import net_r_at_price


class LiquidityTransitionMathTests(unittest.TestCase):
    def test_auction_efficiency_separates_directional_and_choppy_paths(self) -> None:
        directional = auction_efficiency([100.0, 101.0, 102.0, 103.0, 104.0])
        choppy = auction_efficiency([100.0, 103.0, 99.0, 102.0, 100.5])
        self.assertAlmostEqual(directional, 1.0)
        self.assertLess(choppy, 0.10)

    def test_long_cost_aware_target_delivers_requested_net_r(self) -> None:
        entry = 40_000.0
        stop = 39_800.0
        cost_rate = 0.00075
        loss = entry - stop + cost_rate * (entry + stop)
        target = cost_aware_target(entry, 1, loss, 1.8, cost_rate)
        self.assertAlmostEqual(
            net_r_at_price(entry, target, 1, loss, cost_rate),
            1.8,
            places=10,
        )

    def test_short_cost_aware_target_delivers_requested_net_r(self) -> None:
        entry = 40_000.0
        stop = 40_220.0
        cost_rate = 0.00075
        loss = stop - entry + cost_rate * (entry + stop)
        target = cost_aware_target(entry, -1, loss, 1.6, cost_rate)
        self.assertAlmostEqual(
            net_r_at_price(entry, target, -1, loss, cost_rate),
            1.6,
            places=10,
        )

    def test_quantity_is_floored_to_instrument_precision(self) -> None:
        self.assertEqual(floor_quantity(1.23499, 3), 1.234)
        self.assertEqual(floor_quantity(0.0009, 3), 0.0)
        self.assertEqual(floor_quantity(float("nan"), 3), 0.0)

    def test_zero_planned_loss_is_rejected(self) -> None:
        result = net_r_at_price(100.0, 101.0, 1, 0.0, 0.001)
        self.assertTrue(math.isinf(result))
        self.assertLess(result, 0.0)


if __name__ == "__main__":
    unittest.main()
