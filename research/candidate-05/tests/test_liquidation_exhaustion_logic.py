from __future__ import annotations

import unittest

from liquidation_exhaustion_logic import liquidation_exhaustion_side
from liquidation_exhaustion_logic import liquidation_tail_reversal_confirmed


class LiquidationExhaustionLogicTests(unittest.TestCase):
    def test_up_and_down_impulses_are_mirror_symmetric(self) -> None:
        self.assertEqual(
            liquidation_exhaustion_side(
                perpetual_move_atr=0.8,
                perp_minus_spot_return_bps=3.0,
                oi_change_5m=-0.01,
                spot_flow_3m=-0.10,
            ),
            -1,
        )
        self.assertEqual(
            liquidation_exhaustion_side(
                perpetual_move_atr=-0.8,
                perp_minus_spot_return_bps=-3.0,
                oi_change_5m=-0.01,
                spot_flow_3m=0.10,
            ),
            1,
        )

    def test_each_impulse_component_can_veto(self) -> None:
        base = {
            "perpetual_move_atr": -0.8,
            "perp_minus_spot_return_bps": -3.0,
            "oi_change_5m": -0.01,
            "spot_flow_3m": 0.10,
        }
        for key, value in {
            "perpetual_move_atr": -0.2,
            "perp_minus_spot_return_bps": 3.0,
            "oi_change_5m": 0.01,
            "spot_flow_3m": -0.10,
        }.items():
            with self.subTest(key=key):
                trial = dict(base)
                trial[key] = value
                self.assertEqual(liquidation_exhaustion_side(**trial), 0)

    def test_tail_reversal_requires_flow_improvement_and_depth(self) -> None:
        self.assertTrue(
            liquidation_tail_reversal_confirmed(
                side=1,
                flow_15s=0.30,
                flow_60s=-0.30,
                depth_imbalance=0.25,
            ),
        )
        self.assertFalse(
            liquidation_tail_reversal_confirmed(
                side=1,
                flow_15s=0.10,
                flow_60s=-0.10,
                depth_imbalance=0.25,
            ),
        )
        self.assertFalse(
            liquidation_tail_reversal_confirmed(
                side=1,
                flow_15s=0.30,
                flow_60s=-0.30,
                depth_imbalance=-0.25,
            ),
        )


if __name__ == "__main__":
    unittest.main()
