from __future__ import annotations

import unittest

from daily_value_failed_auction_logic import CompletedDailyValue
from daily_value_failed_auction_logic import daily_value_target_candidates
from daily_value_failed_auction_logic import failed_auction_side


class DailyValueFailedAuctionLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = CompletedDailyValue(
            day="2024-01-01",
            high=110.0,
            low=90.0,
            vwap=101.0,
        )

    def test_failed_high_and_low_are_mirror_symmetric(self) -> None:
        short = failed_auction_side(
            previous_close=105.0,
            high=111.0,
            low=108.0,
            close=109.5,
            reference=self.reference,
            atr=3.0,
        )
        long = failed_auction_side(
            previous_close=95.0,
            high=92.0,
            low=89.0,
            close=90.5,
            reference=self.reference,
            atr=3.0,
        )
        self.assertEqual(short, -1)
        self.assertEqual(long, 1)

    def test_two_sided_or_unreclaimed_interaction_is_unresolved(self) -> None:
        self.assertEqual(
            failed_auction_side(
                previous_close=100.0,
                high=112.0,
                low=88.0,
                close=100.0,
                reference=self.reference,
                atr=3.0,
            ),
            0,
        )
        self.assertEqual(
            failed_auction_side(
                previous_close=105.0,
                high=112.0,
                low=109.0,
                close=111.0,
                reference=self.reference,
                atr=3.0,
            ),
            0,
        )

    def test_vwap_precedes_opposite_extreme_as_natural_target(self) -> None:
        long_targets = daily_value_target_candidates(
            side=1,
            entry=90.0,
            reference=self.reference,
        )
        short_targets = daily_value_target_candidates(
            side=-1,
            entry=110.0,
            reference=self.reference,
        )
        self.assertEqual(long_targets[0], ("PREVIOUS_DAY_VWAP:2024-01-01", 101.0))
        self.assertEqual(long_targets[1], ("PREVIOUS_DAY_HIGH:2024-01-01", 110.0))
        self.assertEqual(short_targets[0], ("PREVIOUS_DAY_VWAP:2024-01-01", 101.0))
        self.assertEqual(short_targets[1], ("PREVIOUS_DAY_LOW:2024-01-01", 90.0))


if __name__ == "__main__":
    unittest.main()
