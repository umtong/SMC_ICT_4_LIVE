from __future__ import annotations

import math
import unittest

from sponsored_choch_logic import slippage_protected_marketable_limit
from sponsored_choch_logic import sponsored_choch_participation_ready


class SponsoredChochLogicTest(unittest.TestCase):
    def test_ordinary_participation_requires_active_aligned_flow_and_current_depth(self) -> None:
        base = dict(
            flow_state="ACTIVE_CONFIRMATION",
            side=1,
            flow_15s=0.20,
            current_depth_imbalance=0.12,
            setup_depth_imbalance=0.30,
            target_handoff=False,
            minimum_depth=0.10,
        )
        self.assertTrue(sponsored_choch_participation_ready(**base))
        self.assertFalse(
            sponsored_choch_participation_ready(
                **{**base, "flow_15s": -0.01},
            ),
        )
        self.assertFalse(
            sponsored_choch_participation_ready(
                **{**base, "current_depth_imbalance": 0.09},
            ),
        )
        self.assertFalse(
            sponsored_choch_participation_ready(
                **{**base, "flow_state": "PASSIVE_ROTATION"},
            ),
        )

    def test_target_handoff_uses_recent_reclaim_depth(self) -> None:
        self.assertTrue(
            sponsored_choch_participation_ready(
                flow_state="ACTIVE_CONFIRMATION",
                side=-1,
                flow_15s=-0.25,
                current_depth_imbalance=0.18,
                setup_depth_imbalance=-0.13,
                target_handoff=True,
                minimum_depth=0.10,
            ),
        )
        self.assertFalse(
            sponsored_choch_participation_ready(
                flow_state="ACTIVE_CONFIRMATION",
                side=-1,
                flow_15s=-0.25,
                current_depth_imbalance=-0.13,
                setup_depth_imbalance=0.18,
                target_handoff=True,
                minimum_depth=0.10,
            ),
        )

    def test_readiness_is_mirror_symmetric(self) -> None:
        long_ready = sponsored_choch_participation_ready(
            flow_state="ACTIVE_CONFIRMATION",
            side=1,
            flow_15s=0.30,
            current_depth_imbalance=0.15,
            setup_depth_imbalance=0.20,
            target_handoff=False,
            minimum_depth=0.10,
        )
        short_ready = sponsored_choch_participation_ready(
            flow_state="ACTIVE_CONFIRMATION",
            side=-1,
            flow_15s=-0.30,
            current_depth_imbalance=-0.15,
            setup_depth_imbalance=-0.20,
            target_handoff=False,
            minimum_depth=0.10,
        )
        self.assertTrue(long_ready)
        self.assertEqual(long_ready, short_ready)

    def test_marketable_limit_rounds_through_slippage_symmetrically(self) -> None:
        long_limit = slippage_protected_marketable_limit(
            observed_price=100.0,
            side=1,
            adverse_slippage_rate=0.00025,
            price_increment=0.01,
        )
        short_limit = slippage_protected_marketable_limit(
            observed_price=100.0,
            side=-1,
            adverse_slippage_rate=0.00025,
            price_increment=0.01,
        )
        self.assertAlmostEqual(long_limit, 100.03)
        self.assertAlmostEqual(short_limit, 99.97)
        self.assertGreaterEqual(long_limit, 100.0 * 1.00025)
        self.assertLessEqual(short_limit, 100.0 * 0.99975)
        self.assertTrue(
            math.isnan(
                slippage_protected_marketable_limit(
                    observed_price=0.0,
                    side=1,
                    adverse_slippage_rate=0.00025,
                    price_increment=0.01,
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()
