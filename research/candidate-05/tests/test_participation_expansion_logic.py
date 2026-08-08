from __future__ import annotations

import unittest

from participation_expansion_logic import first_expansion_pullback_defended
from participation_expansion_logic import participation_expansion_direction


class ParticipationExpansionLogicTests(unittest.TestCase):
    def test_up_and_down_information_legs_are_mirror_symmetric(self) -> None:
        common = {"path_efficiency": 0.50, "oi_change_5m": 0.002}
        self.assertEqual(
            participation_expansion_direction(
                move_atr=0.8,
                spot_flow_3m=0.30,
                perpetual_flow_3m=0.30,
                spot_return_bps=4.0,
                perp_minus_spot_return_bps=-1.0,
                **common,
            ),
            1,
        )
        self.assertEqual(
            participation_expansion_direction(
                move_atr=-0.8,
                spot_flow_3m=-0.30,
                perpetual_flow_3m=-0.30,
                spot_return_bps=-4.0,
                perp_minus_spot_return_bps=1.0,
                **common,
            ),
            -1,
        )

    def test_each_information_component_can_veto(self) -> None:
        base = {
            "move_atr": 0.8,
            "path_efficiency": 0.50,
            "oi_change_5m": 0.002,
            "spot_flow_3m": 0.30,
            "perpetual_flow_3m": 0.30,
            "spot_return_bps": 4.0,
            "perp_minus_spot_return_bps": -1.0,
        }
        mutations = {
            "move_atr": 0.2,
            "path_efficiency": 0.10,
            "oi_change_5m": -0.002,
            "spot_flow_3m": -0.30,
            "perpetual_flow_3m": -0.30,
            "spot_return_bps": -4.0,
            "perp_minus_spot_return_bps": 2.0,
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                trial = dict(base)
                trial[key] = value
                self.assertEqual(participation_expansion_direction(**trial), 0)

    def test_first_midpoint_defense_is_symmetric(self) -> None:
        self.assertTrue(
            first_expansion_pullback_defended(
                side=1,
                midpoint=100.0,
                high=101.0,
                low=99.5,
                close=100.5,
                flow_15s=0.10,
                depth_imbalance=0.20,
                spot_flow_60s=0.05,
            ),
        )
        self.assertTrue(
            first_expansion_pullback_defended(
                side=-1,
                midpoint=100.0,
                high=100.5,
                low=99.0,
                close=99.5,
                flow_15s=-0.10,
                depth_imbalance=-0.20,
                spot_flow_60s=-0.05,
            ),
        )

    def test_touch_without_cross_market_defense_fails(self) -> None:
        self.assertFalse(
            first_expansion_pullback_defended(
                side=1,
                midpoint=100.0,
                high=101.0,
                low=99.5,
                close=100.5,
                flow_15s=-0.10,
                depth_imbalance=0.20,
                spot_flow_60s=0.05,
            ),
        )
        self.assertFalse(
            first_expansion_pullback_defended(
                side=1,
                midpoint=100.0,
                high=101.0,
                low=99.5,
                close=100.5,
                flow_15s=0.10,
                depth_imbalance=-0.20,
                spot_flow_60s=0.05,
            ),
        )
        self.assertFalse(
            first_expansion_pullback_defended(
                side=1,
                midpoint=100.0,
                high=101.0,
                low=99.5,
                close=100.5,
                flow_15s=0.10,
                depth_imbalance=0.20,
                spot_flow_60s=-0.05,
            ),
        )


if __name__ == "__main__":
    unittest.main()
