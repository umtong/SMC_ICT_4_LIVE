from __future__ import annotations

import unittest

from funding_window_router import is_funding_window_seed_time
from funding_window_router import reset_confirmed
from funding_window_router import seed_side


class FundingWindowRouterTests(unittest.TestCase):
    def test_only_pre_funding_quarter_hours_are_seed_times(self):
        for hour in (7, 15, 23):
            self.assertTrue(is_funding_window_seed_time(hour=hour, minute=45))
            self.assertFalse(is_funding_window_seed_time(hour=hour, minute=30))
        self.assertFalse(is_funding_window_seed_time(hour=8, minute=45))

    def test_seed_requires_nonzero_flow_and_above_baseline_participation(self):
        self.assertEqual(
            seed_side(flow_open_10s=0.25, opening_participation_burst=1.01),
            1,
        )
        self.assertEqual(
            seed_side(flow_open_10s=-0.25, opening_participation_burst=1.01),
            -1,
        )
        self.assertEqual(
            seed_side(flow_open_10s=0.25, opening_participation_burst=1.0),
            0,
        )
        self.assertEqual(
            seed_side(flow_open_10s=0.0, opening_participation_burst=2.0),
            0,
        )

    def test_reset_is_strictly_adverse_to_original_imbalance(self):
        self.assertTrue(reset_confirmed(side=1, seed_close=100.0, reset_close=99.0))
        self.assertTrue(reset_confirmed(side=-1, seed_close=100.0, reset_close=101.0))
        self.assertFalse(reset_confirmed(side=1, seed_close=100.0, reset_close=100.0))
        self.assertFalse(reset_confirmed(side=-1, seed_close=100.0, reset_close=99.0))


if __name__ == "__main__":
    unittest.main()
