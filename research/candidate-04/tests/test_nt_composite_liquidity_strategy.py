from __future__ import annotations

import unittest

from nt_composite_liquidity_strategy import SCALES


class CompositeLiquidityScaleTests(unittest.TestCase):
    def test_scales_are_structurally_distinct_and_external_first(self) -> None:
        self.assertEqual(SCALES[0].name, "EXTERNAL_30M")
        self.assertEqual(SCALES[1].name, "INTERNAL_5M")
        self.assertGreater(SCALES[0].liquidity_window, SCALES[1].liquidity_window)
        self.assertGreater(SCALES[0].value_window, SCALES[1].value_window)

    def test_internal_scale_has_stricter_impact_tolerance(self) -> None:
        self.assertLess(
            SCALES[1].max_volume_ratio,
            SCALES[0].max_volume_ratio,
        )


if __name__ == "__main__":
    unittest.main()
