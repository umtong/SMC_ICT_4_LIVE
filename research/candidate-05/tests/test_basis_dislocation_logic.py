from __future__ import annotations

import unittest

from basis_dislocation_logic import basis_dislocation_side
from basis_dislocation_logic import forced_perpetual_dislocation_confirmed
from basis_dislocation_logic import robust_basis_location_scale
from basis_dislocation_logic import spot_implied_perpetual_price


class BasisDislocationLogicTests(unittest.TestCase):
    def test_robust_location_ignores_one_extreme(self) -> None:
        location, scale = robust_basis_location_scale([1.0, 1.1, 0.9, 1.0, 40.0])
        self.assertAlmostEqual(location, 1.0)
        self.assertLess(scale, 0.2)

    def test_premium_and_discount_are_mirror_sides(self) -> None:
        history = [0.0] * 60
        side, location, _ = basis_dislocation_side(
            current_basis_bps=2.0,
            history_bps=history,
        )
        self.assertEqual(side, -1)
        self.assertEqual(location, 0.0)
        side, _, _ = basis_dislocation_side(
            current_basis_bps=-2.0,
            history_bps=history,
        )
        self.assertEqual(side, 1)

    def test_current_observation_is_not_part_of_prior_history(self) -> None:
        side, _, _ = basis_dislocation_side(
            current_basis_bps=100.0,
            history_bps=[0.0] * 59,
        )
        self.assertEqual(side, 0)

    def test_forced_transfer_confirmation_is_symmetric(self) -> None:
        self.assertTrue(
            forced_perpetual_dislocation_confirmed(
                side=-1,
                perp_minus_spot_return_bps=5.0,
                oi_change_5m=-0.01,
                flow_15s=-0.30,
                flow_60s=0.30,
                depth_imbalance=-0.25,
            ),
        )
        self.assertTrue(
            forced_perpetual_dislocation_confirmed(
                side=1,
                perp_minus_spot_return_bps=-5.0,
                oi_change_5m=-0.01,
                flow_15s=0.30,
                flow_60s=-0.30,
                depth_imbalance=0.25,
            ),
        )

    def test_each_forced_component_can_veto(self) -> None:
        base = {
            "side": -1,
            "perp_minus_spot_return_bps": 5.0,
            "oi_change_5m": -0.01,
            "flow_15s": -0.30,
            "flow_60s": 0.30,
            "depth_imbalance": -0.25,
        }
        mutations = {
            "perp_minus_spot_return_bps": -5.0,
            "oi_change_5m": 0.01,
            "flow_15s": 0.10,
            "depth_imbalance": 0.25,
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                trial = dict(base)
                trial[key] = value
                self.assertFalse(forced_perpetual_dislocation_confirmed(**trial))

    def test_spot_implied_target_uses_normal_basis(self) -> None:
        self.assertAlmostEqual(
            spot_implied_perpetual_price(
                spot_price=100.0,
                normal_basis_bps=0.0,
            ),
            100.0,
        )
        self.assertGreater(
            spot_implied_perpetual_price(
                spot_price=100.0,
                normal_basis_bps=10.0,
            ),
            100.0,
        )


if __name__ == "__main__":
    unittest.main()
