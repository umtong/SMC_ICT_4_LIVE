from __future__ import annotations

import unittest

from depth_logic import directional_depth_support


class DirectionalDepthSupportTest(unittest.TestCase):
    def test_long_and_short_are_mirror_symmetric(self) -> None:
        self.assertTrue(directional_depth_support(side=1, depth_imbalance=0.10))
        self.assertTrue(directional_depth_support(side=-1, depth_imbalance=-0.10))
        self.assertFalse(directional_depth_support(side=1, depth_imbalance=0.0999))
        self.assertFalse(directional_depth_support(side=-1, depth_imbalance=-0.0999))

    def test_opposing_or_missing_depth_fails(self) -> None:
        self.assertFalse(directional_depth_support(side=1, depth_imbalance=-0.30))
        self.assertFalse(directional_depth_support(side=-1, depth_imbalance=0.30))
        self.assertFalse(directional_depth_support(side=1, depth_imbalance=float("nan")))


if __name__ == "__main__":
    unittest.main()
