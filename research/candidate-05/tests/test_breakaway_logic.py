from __future__ import annotations

import math
import unittest

from breakaway_logic import breakaway_depth_state
from breakaway_logic import favorable_depth_ratio


class BreakawayDepthStateTest(unittest.TestCase):
    def test_two_to_one_ratio_is_one_third_directional_imbalance(self) -> None:
        self.assertAlmostEqual(favorable_depth_ratio(side=1, depth_imbalance=1.0 / 3.0), 2.0)
        self.assertAlmostEqual(favorable_depth_ratio(side=-1, depth_imbalance=-1.0 / 3.0), 2.0)
        self.assertTrue(breakaway_depth_state(side=1, depth_imbalance=1.0 / 3.0))
        self.assertTrue(breakaway_depth_state(side=-1, depth_imbalance=-1.0 / 3.0))

    def test_weaker_or_opposing_book_is_not_breakaway(self) -> None:
        self.assertFalse(breakaway_depth_state(side=1, depth_imbalance=0.32))
        self.assertFalse(breakaway_depth_state(side=-1, depth_imbalance=-0.32))
        self.assertFalse(breakaway_depth_state(side=1, depth_imbalance=-0.50))
        self.assertFalse(breakaway_depth_state(side=-1, depth_imbalance=0.50))
        self.assertFalse(breakaway_depth_state(side=1, depth_imbalance=math.nan))


if __name__ == "__main__":
    unittest.main()
