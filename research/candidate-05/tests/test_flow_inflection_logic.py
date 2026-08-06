from __future__ import annotations

import unittest

from flow_inflection_logic import choch_flow_state
from flow_inflection_logic import directional_tail_improvement
from flow_inflection_logic import sweep_tail_recovers


class FlowInflectionLogicTest(unittest.TestCase):
    def test_sweep_tail_improvement_is_mirror_symmetric(self) -> None:
        long_value = directional_tail_improvement(side=1, flow_15s=-0.05, flow_60s=-0.20)
        short_value = directional_tail_improvement(side=-1, flow_15s=0.05, flow_60s=0.20)
        self.assertAlmostEqual(long_value, 0.15)
        self.assertAlmostEqual(short_value, long_value)
        self.assertTrue(sweep_tail_recovers(side=1, flow_15s=-0.05, flow_60s=-0.20))
        self.assertTrue(sweep_tail_recovers(side=-1, flow_15s=0.05, flow_60s=0.20))

    def test_choch_active_confirmation_is_mirror_symmetric(self) -> None:
        long_state = choch_flow_state(
            side=1,
            flow_15s=0.20,
            flow_3m=0.10,
            depth_imbalance=0.15,
        )
        short_state = choch_flow_state(
            side=-1,
            flow_15s=-0.20,
            flow_3m=-0.10,
            depth_imbalance=-0.15,
        )
        self.assertEqual(long_state, "ACTIVE_CONFIRMATION")
        self.assertEqual(short_state, long_state)

    def test_passive_rotation_requires_supportive_depth(self) -> None:
        self.assertEqual(
            choch_flow_state(
                side=1,
                flow_15s=-0.15,
                flow_3m=0.05,
                depth_imbalance=0.20,
            ),
            "PASSIVE_ROTATION",
        )
        self.assertIsNone(
            choch_flow_state(
                side=1,
                flow_15s=-0.15,
                flow_3m=0.05,
                depth_imbalance=0.05,
            ),
        )

    def test_mature_three_minute_move_is_not_early_choch(self) -> None:
        self.assertIsNone(
            choch_flow_state(
                side=1,
                flow_15s=0.80,
                flow_3m=0.55,
                depth_imbalance=0.30,
            ),
        )


if __name__ == "__main__":
    unittest.main()
