from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v70_participation_expansion import BRANCH
from strategy_v70_participation_expansion import ParticipationExpansionStrategy


class ParticipationExpansionContractTests(unittest.TestCase):
    def test_v70_is_additive_over_frozen_v46(self) -> None:
        self.assertTrue(
            issubclass(
                ParticipationExpansionStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )

    def test_parent_has_priority_and_signal_bar_cannot_be_pullback(self) -> None:
        on_bar = inspect.getsource(ParticipationExpansionStrategy.on_bar)
        advance = inspect.getsource(
            ParticipationExpansionStrategy._advance_participation_watch,
        )
        self.assertLess(on_bar.index("super().on_bar"), on_bar.index("_advance_participation_watch"))
        self.assertIn("self.bar_index <= watch.created_index", advance)

    def test_only_completed_five_minute_boundaries_create_signals(self) -> None:
        source = inspect.getsource(
            ParticipationExpansionStrategy._observe_five_minute_expansion,
        )
        self.assertIn("minute % 5 != 4", source)
        self.assertIn("selected = rows[-6:]", source)
        self.assertIn("oi_change_5m", source)

    def test_execution_uses_live_pool_and_inherited_bracket(self) -> None:
        source = inspect.getsource(
            ParticipationExpansionStrategy._submit_participation_pullback,
        )
        self.assertIn("choose_liquidity_target", source)
        self.assertIn('target_source.startswith("POOL:")', source)
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertIn(BRANCH, source)
        self.assertNotIn("order_factory", source)


if __name__ == "__main__":
    unittest.main()
