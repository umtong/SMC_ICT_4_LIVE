from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v68_liquidation_exhaustion import BRANCH
from strategy_v68_liquidation_exhaustion import LiquidationExhaustionStrategy


class LiquidationExhaustionContractTests(unittest.TestCase):
    def test_v68_is_additive_over_frozen_v46(self) -> None:
        self.assertTrue(
            issubclass(
                LiquidationExhaustionStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )

    def test_parent_has_priority_and_current_basis_is_prior_excluded(self) -> None:
        source = inspect.getsource(LiquidationExhaustionStrategy.on_bar)
        self.assertLess(source.index("super().on_bar"), source.index("_consider_liquidation_exhaustion"))
        self.assertLess(source.index("_consider_liquidation_exhaustion"), source.index("liquidation_basis_history.append"))

    def test_stop_uses_completed_five_minute_impulse(self) -> None:
        source = inspect.getsource(
            LiquidationExhaustionStrategy._submit_liquidation_exhaustion,
        )
        self.assertIn("impulse_low", source)
        self.assertIn("impulse_high", source)
        self.assertIn("spot_implied_perpetual_price", source)

    def test_execution_remains_inherited_price_capped_bracket(self) -> None:
        source = inspect.getsource(
            LiquidationExhaustionStrategy._submit_liquidation_exhaustion,
        )
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertIn(BRANCH, source)
        self.assertNotIn("order_factory", source)


if __name__ == "__main__":
    unittest.main()
