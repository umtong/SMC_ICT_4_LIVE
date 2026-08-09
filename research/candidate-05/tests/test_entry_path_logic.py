from __future__ import annotations

import unittest

from flow_inflection_logic import breakaway_follow_through
from flow_inflection_logic import has_adverse_slippage_room
from flow_inflection_logic import worst_entry_preserving_net_r


class EntryPathLogicTest(unittest.TestCase):
    def test_breakaway_follow_through_is_mirror_symmetric(self) -> None:
        long_passes = breakaway_follow_through(
            side=1,
            choch_close=100.0,
            current_close=102.0,
            atr=2.0,
            sweep_depth_imbalance=1.0 / 3.0,
            current_depth_imbalance=0.10,
            current_flow_3m=0.01,
        )
        short_passes = breakaway_follow_through(
            side=-1,
            choch_close=100.0,
            current_close=98.0,
            atr=2.0,
            sweep_depth_imbalance=-1.0 / 3.0,
            current_depth_imbalance=-0.10,
            current_flow_3m=-0.01,
        )
        self.assertTrue(long_passes)
        self.assertEqual(short_passes, long_passes)

    def test_worst_entry_preserves_requested_post_cost_r(self) -> None:
        cost_rate = 0.00075
        slippage_rate = 0.00025
        for side, stop, target in ((1, 90.0, 120.0), (-1, 110.0, 80.0)):
            entry = worst_entry_preserving_net_r(
                stop=stop,
                target=target,
                side=side,
                minimum_net_r=0.40,
                cost_rate=cost_rate,
                adverse_slippage_rate=slippage_rate,
            )
            expected_entry = entry * (1.0 + side * slippage_rate)
            expected_stop = stop * (1.0 - side * slippage_rate)
            planned_loss = side * (expected_entry - expected_stop) + cost_rate * (
                expected_entry + expected_stop
            )
            net = side * (target - entry) - cost_rate * (entry + target)
            self.assertAlmostEqual(net / planned_loss, 0.40, places=8)

    def test_marketability_requires_configured_slippage_headroom(self) -> None:
        self.assertTrue(
            has_adverse_slippage_room(
                observed_price=100.0,
                limit_price=100.03,
                side=1,
                adverse_slippage_rate=0.00025,
            ),
        )
        self.assertFalse(
            has_adverse_slippage_room(
                observed_price=100.0,
                limit_price=100.02,
                side=1,
                adverse_slippage_rate=0.00025,
            ),
        )
        self.assertTrue(
            has_adverse_slippage_room(
                observed_price=100.0,
                limit_price=99.97,
                side=-1,
                adverse_slippage_rate=0.00025,
            ),
        )


if __name__ == "__main__":
    unittest.main()
