from __future__ import annotations

import math
import unittest

from positioning_reset_logic import completed_path_efficiency
from positioning_reset_logic import positioning_reset_supports_early_reversal


class PositioningResetLogicTest(unittest.TestCase):
    def test_path_efficiency_is_net_over_path(self) -> None:
        self.assertAlmostEqual(completed_path_efficiency([100, 102, 101, 104]), 4 / 6)
        self.assertEqual(completed_path_efficiency([100, 100]), 0.0)

    def test_long_reset_requires_positive_basis_normalization(self) -> None:
        self.assertTrue(
            positioning_reset_supports_early_reversal(
                side=1,
                sweep_premium_change_5m=0.0002,
                sweep_path_efficiency_30m=0.30,
                choch_oi_change_15m=0.0005,
            ),
        )
        self.assertFalse(
            positioning_reset_supports_early_reversal(
                side=1,
                sweep_premium_change_5m=-0.0002,
                sweep_path_efficiency_30m=0.30,
                choch_oi_change_15m=0.0005,
            ),
        )

    def test_short_reset_is_mirror_symmetric(self) -> None:
        self.assertTrue(
            positioning_reset_supports_early_reversal(
                side=-1,
                sweep_premium_change_5m=-0.0002,
                sweep_path_efficiency_30m=0.25,
                choch_oi_change_15m=-0.002,
            ),
        )

    def test_material_oi_expansion_and_chop_are_rejected(self) -> None:
        self.assertFalse(
            positioning_reset_supports_early_reversal(
                side=1,
                sweep_premium_change_5m=0.0002,
                sweep_path_efficiency_30m=0.24,
                choch_oi_change_15m=0.0,
            ),
        )
        self.assertFalse(
            positioning_reset_supports_early_reversal(
                side=1,
                sweep_premium_change_5m=0.0002,
                sweep_path_efficiency_30m=0.30,
                choch_oi_change_15m=0.0011,
            ),
        )
        self.assertFalse(
            positioning_reset_supports_early_reversal(
                side=1,
                sweep_premium_change_5m=math.nan,
                sweep_path_efficiency_30m=0.30,
                choch_oi_change_15m=0.0,
            ),
        )


if __name__ == "__main__":
    unittest.main()
