from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v58_forced_basis_reversion import BRANCH
from strategy_v58_forced_basis_reversion import ForcedBasisReversionStrategy


class ForcedBasisReversionContractTests(unittest.TestCase):
    def test_v58_is_additive_over_frozen_v46(self) -> None:
        self.assertTrue(
            issubclass(
                ForcedBasisReversionStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )

    def test_parent_processes_bar_before_new_family(self) -> None:
        source = inspect.getsource(ForcedBasisReversionStrategy.on_bar)
        self.assertLess(
            source.index("super().on_bar"),
            source.index("_consider_basis_dislocation"),
        )

    def test_target_is_spot_implied_not_fixed_r(self) -> None:
        source = inspect.getsource(
            ForcedBasisReversionStrategy._submit_basis_reversion,
        )
        self.assertIn("spot_implied_perpetual_price", source)
        self.assertIn("SPOT_IMPLIED_TRAILING_BASIS_MEDIAN", source)
        self.assertNotIn("cost_aware_target", source)

    def test_execution_uses_inherited_price_capped_bracket(self) -> None:
        source = inspect.getsource(
            ForcedBasisReversionStrategy._submit_basis_reversion,
        )
        self.assertEqual(BRANCH, "FORCED_SPOT_PERP_BASIS_REVERSION")
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertIn("branch=BRANCH", source)
        self.assertNotIn("order_factory", source)


if __name__ == "__main__":
    unittest.main()
