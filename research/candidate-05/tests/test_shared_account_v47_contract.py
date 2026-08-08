from __future__ import annotations

import unittest

# Import the adapter first so it installs the same feature contracts as the
# executable runner before shared_account_backtest captures features.load_range.
from shared_account_backtest_v47 import V47_WINNER
from shared_account_backtest_v47 import _strategy_path
import shared_account_backtest as base_runner
from shared_account_v47_variants import FinalSharedV47BTCUSDTStrategy
from shared_account_v47_variants import v47_shared_strategy_path
from strategy_global_slot_wrappers_v4 import SharedAccountEntryLifecycleMixin
from strategy_v47_relative_value import RelativeValueDislocationStrategy


class SharedAccountV47ContractTests(unittest.TestCase):
    def test_v47_variant_combines_frozen_policy_and_global_slot(self) -> None:
        self.assertTrue(issubclass(FinalSharedV47BTCUSDTStrategy, RelativeValueDislocationStrategy))
        self.assertTrue(issubclass(FinalSharedV47BTCUSDTStrategy, SharedAccountEntryLifecycleMixin))

    def test_all_project_symbols_resolve_to_importable_v47_paths(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
            path = v47_shared_strategy_path(symbol)
            self.assertEqual(path, _strategy_path(V47_WINNER, symbol))
            self.assertIn(symbol, path)

    def test_existing_shared_variants_remain_unchanged(self) -> None:
        winner = "strategy_v46_no_post_retrace_breakaway:NoPostRetraceBreakawayStrategy"
        self.assertEqual(
            _strategy_path(winner, "BTCUSDT"),
            "shared_account_strategy_variants_v2:FinalSharedV46BTCUSDTStrategy",
        )
        self.assertIs(base_runner.final_shared_strategy_path, _strategy_path)


if __name__ == "__main__":
    unittest.main()
