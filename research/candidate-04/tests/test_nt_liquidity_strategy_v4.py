from __future__ import annotations

import unittest

from nt_liquidity_strategy_v4 import accepted_retest_price


class AcceptedRetestPriceTests(unittest.TestCase):
    def test_long_retest_never_falls_below_accepted_extreme(self) -> None:
        self.assertEqual(
            accepted_retest_price(100.0, 110.0, 107.0, 1),
            107.0,
        )
        self.assertEqual(
            accepted_retest_price(100.0, 110.0, 103.0, 1),
            105.0,
        )

    def test_short_retest_never_rises_above_accepted_extreme(self) -> None:
        self.assertEqual(
            accepted_retest_price(110.0, 100.0, 103.0, -1),
            103.0,
        )
        self.assertEqual(
            accepted_retest_price(110.0, 100.0, 108.0, -1),
            105.0,
        )


if __name__ == "__main__":
    unittest.main()
