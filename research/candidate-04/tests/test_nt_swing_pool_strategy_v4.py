from __future__ import annotations

import unittest

from nt_swing_pool_strategy_v4 import select_causal_target


class CausalTargetSelectionTests(unittest.TestCase):
    def test_long_skips_targets_behind_entry_and_too_close(self) -> None:
        selected = select_causal_target(
            [99.0, 100.5, 103.0, 106.0],
            entry=100.0,
            side=1,
            planned_loss_per_unit=2.0,
            cost_rate=0.0,
            minimum_net_r=1.2,
        )
        self.assertEqual(selected, 103.0)

    def test_short_selects_nearest_valid_lower_target(self) -> None:
        selected = select_causal_target(
            [104.0, 99.0, 97.0, 94.0],
            entry=100.0,
            side=-1,
            planned_loss_per_unit=2.0,
            cost_rate=0.0,
            minimum_net_r=1.2,
        )
        self.assertEqual(selected, 97.0)

    def test_none_when_no_directional_target_meets_floor(self) -> None:
        selected = select_causal_target(
            [99.0, 100.5, 101.0],
            entry=100.0,
            side=1,
            planned_loss_per_unit=2.0,
            cost_rate=0.0,
            minimum_net_r=1.2,
        )
        self.assertIsNone(selected)


if __name__ == "__main__":
    unittest.main()
