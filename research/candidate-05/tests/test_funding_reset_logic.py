from __future__ import annotations

from datetime import datetime, timezone
import unittest

from funding_reset_logic import funding_cycle_key
from funding_reset_logic import funding_forced_reset_confirmed
from funding_reset_logic import funding_reset_side
from funding_reset_logic import in_post_funding_window
from funding_reset_logic import minutes_after_funding


def ns(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)


class FundingResetLogicTests(unittest.TestCase):
    def test_fixed_eight_hour_cycles_and_window(self) -> None:
        self.assertEqual(minutes_after_funding(ns("2024-01-01T08:06:00")), 6)
        self.assertTrue(in_post_funding_window(ns("2024-01-01T08:06:00")))
        self.assertTrue(in_post_funding_window(ns("2024-01-01T16:20:00")))
        self.assertFalse(in_post_funding_window(ns("2024-01-01T16:05:00")))
        self.assertFalse(in_post_funding_window(ns("2024-01-01T16:21:00")))
        self.assertNotEqual(
            funding_cycle_key(ns("2024-01-01T07:59:00")),
            funding_cycle_key(ns("2024-01-01T08:00:00")),
        )

    def test_crowded_long_and_short_resets_are_symmetric(self) -> None:
        self.assertEqual(
            funding_reset_side(
                pre_funding_basis_bps=3.0,
                normal_basis_bps=1.0,
                perp_minus_spot_return_bps=-2.0,
            ),
            1,
        )
        self.assertEqual(
            funding_reset_side(
                pre_funding_basis_bps=-3.0,
                normal_basis_bps=-1.0,
                perp_minus_spot_return_bps=2.0,
            ),
            -1,
        )

    def test_wrong_post_settlement_move_is_ambiguous(self) -> None:
        self.assertEqual(
            funding_reset_side(
                pre_funding_basis_bps=3.0,
                normal_basis_bps=1.0,
                perp_minus_spot_return_bps=2.0,
            ),
            0,
        )

    def test_forced_reset_requires_oi_tail_and_depth(self) -> None:
        base = {
            "side": 1,
            "oi_change_5m": -0.01,
            "flow_15s": 0.30,
            "flow_60s": -0.30,
            "depth_imbalance": 0.25,
        }
        self.assertTrue(funding_forced_reset_confirmed(**base))
        for key, value in {
            "oi_change_5m": 0.01,
            "flow_15s": -0.10,
            "depth_imbalance": -0.25,
        }.items():
            with self.subTest(key=key):
                trial = dict(base)
                trial[key] = value
                self.assertFalse(funding_forced_reset_confirmed(**trial))


if __name__ == "__main__":
    unittest.main()
