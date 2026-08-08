from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v73_daily_value_failed_auction import BRANCH
from strategy_v73_daily_value_failed_auction import DailyValueFailedAuctionStrategy


class DailyValueFailedAuctionContractTests(unittest.TestCase):
    def test_v73_is_additive_over_unchanged_v46(self) -> None:
        self.assertTrue(
            issubclass(
                DailyValueFailedAuctionStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )

    def test_parent_receives_bar_before_daily_family(self) -> None:
        source = inspect.getsource(DailyValueFailedAuctionStrategy.on_bar)
        self.assertLess(source.index("super().on_bar"), source.index("_advance_daily_value"))
        self.assertLess(source.index("_advance_daily_value"), source.index("_consider_daily_failure"))

    def test_entry_stop_and_target_share_daily_auction_leg(self) -> None:
        source = inspect.getsource(
            DailyValueFailedAuctionStrategy._submit_daily_value_failure,
        )
        self.assertEqual(BRANCH, "DAILY_VALUE_FAILED_AUCTION")
        self.assertIn("reference.low if side > 0 else reference.high", source)
        self.assertIn("daily_value_target_candidates", source)
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertNotIn("order_factory", source)

    def test_first_material_failure_owns_each_side(self) -> None:
        source = inspect.getsource(
            DailyValueFailedAuctionStrategy._consider_daily_failure,
        )
        self.assertIn("daily_failed_sides_consumed.add(side)", source)
        self.assertLess(
            source.index("daily_failed_sides_consumed.add(side)"),
            source.index("inventory_trap_confirmed"),
        )


if __name__ == "__main__":
    unittest.main()
