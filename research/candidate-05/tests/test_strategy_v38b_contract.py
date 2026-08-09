from __future__ import annotations

import inspect
import unittest

from shared_account_strategy_variants_v38b import WINNER
from shared_account_strategy_variants_v38b import experimental_shared_strategy_class
from strategy_global_slot_wrappers_v4 import SharedAccountEntryLifecycleMixin
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v38_isolated_smt_reversal import IsolatedSmtReversalStrategy
from strategy_v38b_reachable_isolated_smt import ReachableIsolatedSmtReversalStrategy


class StrategyV38bContractTest(unittest.TestCase):
    def test_v38b_changes_only_target_reachability_before_v38_execution(self) -> None:
        self.assertTrue(
            issubclass(
                ReachableIsolatedSmtReversalStrategy,
                IsolatedSmtReversalStrategy,
            ),
        )
        self.assertTrue(
            issubclass(ReachableIsolatedSmtReversalStrategy, ScenarioValidEntryStrategy),
        )
        names = set(ReachableIsolatedSmtReversalStrategy.__dict__)
        self.assertEqual(
            names & {
                "_equity_value",
                "_submit_price_capped_bracket",
                "_frozen_target_price",
                "on_position_opened",
                "on_position_closed",
            },
            set(),
        )
        source = inspect.getsource(
            ReachableIsolatedSmtReversalStrategy._submit_isolated_price_cap,
        )
        self.assertIn("measured_move_target_reachability", source)
        self.assertIn("super()._submit_isolated_price_cap", source)
        self.assertNotIn("risk_fraction", source)
        self.assertNotIn("target =", source)

    def test_all_symbols_use_global_slot_before_v38b_market_logic(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
            cls = experimental_shared_strategy_class(WINNER, symbol)
            mro = cls.mro()
            self.assertLess(
                mro.index(SharedAccountEntryLifecycleMixin),
                mro.index(ReachableIsolatedSmtReversalStrategy),
            )
            self.assertLess(
                mro.index(ReachableIsolatedSmtReversalStrategy),
                mro.index(ScenarioValidEntryStrategy),
            )

    def test_branch_identity_is_distinct_from_v38(self) -> None:
        self.assertEqual(
            ReachableIsolatedSmtReversalStrategy.BRANCH,
            "SMT_REACHABLE_ISOLATED_REVERSAL",
        )
        self.assertNotEqual(
            ReachableIsolatedSmtReversalStrategy.BRANCH,
            IsolatedSmtReversalStrategy.BRANCH,
        )


if __name__ == "__main__":
    unittest.main()
