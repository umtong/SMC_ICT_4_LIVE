from __future__ import annotations

import unittest

from balance_acceptance_logic import BALANCE_ACCEPTANCE_HOLD_BARS
from balance_acceptance_logic import balance_retest_confirms_acceptance
from balance_acceptance_logic import closes_outside_balance
from balance_acceptance_logic import depth_migration_sponsors_acceptance


class BalanceAcceptanceLogicTest(unittest.TestCase):
    def test_hold_horizon_matches_three_minute_flow_state(self) -> None:
        self.assertEqual(BALANCE_ACCEPTANCE_HOLD_BARS, 3)

    def test_depth_migration_is_mirror_symmetric(self) -> None:
        long_ready = depth_migration_sponsors_acceptance(
            side=1,
            depth_imbalance=0.15,
            bid_depth_change_5m=0.02,
            ask_depth_change_5m=-0.04,
            minimum_directional_depth=0.10,
        )
        short_ready = depth_migration_sponsors_acceptance(
            side=-1,
            depth_imbalance=-0.15,
            bid_depth_change_5m=-0.04,
            ask_depth_change_5m=0.02,
            minimum_directional_depth=0.10,
        )
        self.assertTrue(long_ready)
        self.assertEqual(long_ready, short_ready)
        self.assertFalse(
            depth_migration_sponsors_acceptance(
                side=1,
                depth_imbalance=0.15,
                bid_depth_change_5m=-0.01,
                ask_depth_change_5m=-0.04,
                minimum_directional_depth=0.10,
            ),
        )

    def test_outside_balance_is_mirror_symmetric(self) -> None:
        self.assertTrue(
            closes_outside_balance(
                side=1,
                close=101.0,
                balance_high=100.0,
                balance_low=90.0,
            ),
        )
        self.assertTrue(
            closes_outside_balance(
                side=-1,
                close=89.0,
                balance_high=100.0,
                balance_low=90.0,
            ),
        )

    def test_retest_requires_touch_outside_close_flow_and_depth(self) -> None:
        base = dict(
            side=1,
            high=102.0,
            low=100.5,
            close=101.9,
            balance_high=100.0,
            balance_low=90.0,
            atr=5.0,
            flow_15s=0.20,
            depth_imbalance=0.15,
            retrace_tolerance_atr=0.18,
            minimum_close_location=0.54,
            minimum_directional_depth=0.10,
        )
        self.assertTrue(balance_retest_confirms_acceptance(**base))
        self.assertFalse(
            balance_retest_confirms_acceptance(
                **{**base, "low": 101.1},
            ),
        )
        self.assertFalse(
            balance_retest_confirms_acceptance(
                **{**base, "close": 99.9},
            ),
        )
        self.assertFalse(
            balance_retest_confirms_acceptance(
                **{**base, "flow_15s": -0.01},
            ),
        )
        self.assertFalse(
            balance_retest_confirms_acceptance(
                **{**base, "depth_imbalance": 0.09},
            ),
        )

    def test_retest_is_mirror_symmetric(self) -> None:
        long_ready = balance_retest_confirms_acceptance(
            side=1,
            high=102.0,
            low=100.5,
            close=101.9,
            balance_high=100.0,
            balance_low=90.0,
            atr=5.0,
            flow_15s=0.20,
            depth_imbalance=0.15,
            retrace_tolerance_atr=0.18,
            minimum_close_location=0.54,
            minimum_directional_depth=0.10,
        )
        short_ready = balance_retest_confirms_acceptance(
            side=-1,
            high=89.5,
            low=88.0,
            close=88.1,
            balance_high=100.0,
            balance_low=90.0,
            atr=5.0,
            flow_15s=-0.20,
            depth_imbalance=-0.15,
            retrace_tolerance_atr=0.18,
            minimum_close_location=0.54,
            minimum_directional_depth=0.10,
        )
        self.assertTrue(long_ready)
        self.assertEqual(long_ready, short_ready)


if __name__ == "__main__":
    unittest.main()
