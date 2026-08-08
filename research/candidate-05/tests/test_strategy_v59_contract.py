from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v59_spot_boundary_retest import BRANCH
from strategy_v59_spot_boundary_retest import SpotBoundaryRetestStrategy


class SpotBoundaryRetestContractTests(unittest.TestCase):
    def test_v59_is_additive_over_frozen_v46(self) -> None:
        self.assertTrue(
            issubclass(
                SpotBoundaryRetestStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )

    def test_parent_has_priority_on_every_completed_bar(self) -> None:
        source = inspect.getsource(SpotBoundaryRetestStrategy.on_bar)
        self.assertLess(
            source.index("super().on_bar"),
            source.index("_advance_spot_boundary_watch"),
        )
        self.assertLess(
            source.index("_advance_spot_boundary_watch"),
            source.index("_observe_new_spot_boundary"),
        )

    def test_signal_bar_cannot_be_the_retest_bar(self) -> None:
        source = inspect.getsource(
            SpotBoundaryRetestStrategy._advance_spot_boundary_watch,
        )
        self.assertIn("self.bar_index <= watch.created_index", source)
        self.assertIn("self.bar_index <= watch.accepted_index", source)

    def test_target_is_still_live_opposing_liquidity(self) -> None:
        source = inspect.getsource(
            SpotBoundaryRetestStrategy._submit_spot_boundary_retest,
        )
        self.assertIn("choose_liquidity_target", source)
        self.assertIn('target_source.startswith("POOL:")', source)
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertIn(BRANCH, source)
        self.assertNotIn("order_factory", source)

    def test_first_touch_is_terminal_even_when_defense_fails(self) -> None:
        source = inspect.getsource(
            SpotBoundaryRetestStrategy._advance_spot_boundary_watch,
        )
        self.assertIn('spot_boundary_first_touches', source)
        self.assertIn(
            "FIRST_BOUNDARY_RETEST_LACKED_PERPETUAL_FLOW_DEPTH_DEFENSE",
            source,
        )
        self.assertIn("SPOT_FLOW_REVERSED_AT_FIRST_BOUNDARY_RETEST", source)


if __name__ == "__main__":
    unittest.main()
