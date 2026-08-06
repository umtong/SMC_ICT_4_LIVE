from __future__ import annotations

import math
import unittest

from nt_auction_excess_strategy import weighted_location


class AuctionValueMathTests(unittest.TestCase):
    def test_equal_weights_match_population_mean_and_std(self) -> None:
        mean, std = weighted_location([1.0, 2.0, 3.0], [1.0, 1.0, 1.0])
        self.assertAlmostEqual(mean, 2.0)
        self.assertAlmostEqual(std, math.sqrt(2.0 / 3.0))

    def test_large_volume_controls_fair_value(self) -> None:
        mean, _ = weighted_location([100.0, 110.0], [9.0, 1.0])
        self.assertAlmostEqual(mean, 101.0)

    def test_non_positive_total_weight_is_invalid(self) -> None:
        mean, std = weighted_location([1.0, 2.0], [0.0, -1.0])
        self.assertTrue(math.isnan(mean))
        self.assertTrue(math.isnan(std))


if __name__ == "__main__":
    unittest.main()
