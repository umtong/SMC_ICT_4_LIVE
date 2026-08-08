from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v62_post_funding_reset import BRANCH
from strategy_v62_post_funding_reset import PostFundingForcedResetStrategy


class PostFundingForcedResetContractTests(unittest.TestCase):
    def test_v62_is_additive_over_frozen_v46(self) -> None:
        self.assertTrue(
            issubclass(
                PostFundingForcedResetStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )

    def test_parent_has_priority_before_scheduled_observation(self) -> None:
        source = inspect.getsource(PostFundingForcedResetStrategy.on_bar)
        self.assertLess(
            source.index("super().on_bar"),
            source.index("_consider_post_funding_reset"),
        )

    def test_normal_basis_excludes_last_thirty_crowding_minutes(self) -> None:
        source = inspect.getsource(PostFundingForcedResetStrategy._roll_funding_cycle)
        self.assertIn("history[:-30]", source)
        self.assertIn("cycle_pre_funding_basis", source)

    def test_target_is_spot_implied_frozen_normal_basis(self) -> None:
        source = inspect.getsource(
            PostFundingForcedResetStrategy._submit_post_funding_reset,
        )
        self.assertIn("spot_implied_perpetual_price", source)
        self.assertIn("cycle_normal_basis", source)
        self.assertIn("POST_FUNDING_SPOT_IMPLIED_NORMAL_BASIS", source)
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertIn(BRANCH, source)
        self.assertNotIn("order_factory", source)


if __name__ == "__main__":
    unittest.main()
