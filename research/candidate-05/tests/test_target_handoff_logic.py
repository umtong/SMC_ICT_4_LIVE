from __future__ import annotations

import unittest

from target_handoff_logic import delayed_target_reclaim_ready
from target_handoff_logic import target_exit_matches
from target_handoff_logic import target_sweep_bar_sponsored


class TargetHandoffLogicTest(unittest.TestCase):
    def test_target_exit_allows_only_one_tick_and_positive_pnl(self) -> None:
        self.assertTrue(
            target_exit_matches(
                average_exit=99.9,
                target=100.0,
                price_increment=0.1,
                realized_pnl=10.0,
            ),
        )
        self.assertFalse(
            target_exit_matches(
                average_exit=99.8,
                target=100.0,
                price_increment=0.1,
                realized_pnl=10.0,
            ),
        )
        self.assertFalse(
            target_exit_matches(
                average_exit=99.9,
                target=100.0,
                price_increment=0.1,
                realized_pnl=-1.0,
            ),
        )

    def test_sweep_sponsorship_is_mirror_symmetric(self) -> None:
        high_sweep = target_sweep_bar_sponsored(
            kind="HIGH",
            flow_15s=0.25,
            flow_60s=0.15,
            notional_burst=1.4,
            efficiency_60s=0.20,
            minimum_directional_flow=0.12,
            minimum_notional_burst=1.05,
            maximum_efficiency=0.38,
        )
        low_sweep = target_sweep_bar_sponsored(
            kind="LOW",
            flow_15s=-0.25,
            flow_60s=-0.15,
            notional_burst=1.4,
            efficiency_60s=0.20,
            minimum_directional_flow=0.12,
            minimum_notional_burst=1.05,
            maximum_efficiency=0.38,
        )
        self.assertTrue(high_sweep)
        self.assertEqual(high_sweep, low_sweep)

    def test_delayed_reclaim_is_mirror_symmetric(self) -> None:
        high_reclaim = delayed_target_reclaim_ready(
            kind="HIGH",
            pool_level=100.0,
            accumulated_high=101.0,
            accumulated_low=98.0,
            current_close=99.5,
            atr=5.0,
            sweep_sponsored=True,
            current_efficiency_60s=0.20,
            current_bid_depth_change_1m=-0.02,
            current_ask_depth_change_1m=0.03,
            minimum_penetration_atr=0.08,
            maximum_efficiency=0.38,
            minimum_same_side_refill=0.01,
        )
        low_reclaim = delayed_target_reclaim_ready(
            kind="LOW",
            pool_level=100.0,
            accumulated_high=102.0,
            accumulated_low=99.0,
            current_close=100.5,
            atr=5.0,
            sweep_sponsored=True,
            current_efficiency_60s=0.20,
            current_bid_depth_change_1m=0.03,
            current_ask_depth_change_1m=-0.02,
            minimum_penetration_atr=0.08,
            maximum_efficiency=0.38,
            minimum_same_side_refill=0.01,
        )
        self.assertTrue(high_reclaim)
        self.assertEqual(high_reclaim, low_reclaim)

    def test_reclaim_requires_sponsorship_penetration_and_refill(self) -> None:
        base = dict(
            kind="HIGH",
            pool_level=100.0,
            accumulated_high=101.0,
            accumulated_low=98.0,
            current_close=99.5,
            atr=5.0,
            sweep_sponsored=True,
            current_efficiency_60s=0.20,
            current_bid_depth_change_1m=0.0,
            current_ask_depth_change_1m=0.03,
            minimum_penetration_atr=0.08,
            maximum_efficiency=0.38,
            minimum_same_side_refill=0.01,
        )
        self.assertTrue(delayed_target_reclaim_ready(**base))
        self.assertFalse(delayed_target_reclaim_ready(**{**base, "sweep_sponsored": False}))
        self.assertFalse(delayed_target_reclaim_ready(**{**base, "accumulated_high": 100.2}))
        self.assertFalse(
            delayed_target_reclaim_ready(
                **{**base, "current_ask_depth_change_1m": 0.0},
            ),
        )


if __name__ == "__main__":
    unittest.main()
