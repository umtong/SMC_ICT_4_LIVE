from __future__ import annotations

import unittest

from auction_state_logic import liquidation_breakaway_confirmed
from auction_state_logic import position_building_acceptance
from auction_state_logic import reversal_depth_confirmed


class AuctionStateLogicTest(unittest.TestCase):
    def test_reversal_depth_is_mirror_symmetric(self) -> None:
        self.assertTrue(reversal_depth_confirmed(side=1, sweep_imbalance=0.20, current_imbalance=0.21, pool_age_minutes=30))
        self.assertTrue(reversal_depth_confirmed(side=-1, sweep_imbalance=-0.20, current_imbalance=-0.21, pool_age_minutes=30))
        self.assertFalse(reversal_depth_confirmed(side=1, sweep_imbalance=0.20, current_imbalance=0.10, pool_age_minutes=30))
        self.assertTrue(reversal_depth_confirmed(side=1, sweep_imbalance=0.20, current_imbalance=0.01, pool_age_minutes=5))

    def test_breakaway_requires_liquidation_not_position_building(self) -> None:
        self.assertTrue(liquidation_breakaway_confirmed(side=1, sweep_imbalance=0.40, current_imbalance=0.20, oi_change_sweep_to_confirmation=-0.001))
        self.assertFalse(liquidation_breakaway_confirmed(side=1, sweep_imbalance=0.40, current_imbalance=0.20, oi_change_sweep_to_confirmation=0.001))
        self.assertTrue(liquidation_breakaway_confirmed(side=-1, sweep_imbalance=-0.40, current_imbalance=-0.20, oi_change_sweep_to_confirmation=0.0))

    def test_position_building_acceptance_uses_sign_causality(self) -> None:
        kwargs = dict(
            accepted_distance_atr=0.01,
            directional_flow_15s=0.01,
            directional_flow_60s=0.01,
            efficiency_60s=0.15,
            consumed_side_depth_change=-0.001,
            oi_change_15m=0.001,
        )
        self.assertTrue(position_building_acceptance(**kwargs))
        self.assertFalse(position_building_acceptance(**{**kwargs, "oi_change_15m": -0.001}))
        self.assertFalse(position_building_acceptance(**{**kwargs, "consumed_side_depth_change": 0.001}))


if __name__ == "__main__":
    unittest.main()
