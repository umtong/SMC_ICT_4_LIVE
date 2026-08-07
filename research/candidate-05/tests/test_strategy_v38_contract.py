from __future__ import annotations

import inspect
import unittest

from shared_account_strategy_variants_v38 import WINNER
from shared_account_strategy_variants_v38 import experimental_shared_strategy_class
from shared_account_strategy_variants_v38 import experimental_shared_strategy_path
from strategy_global_slot_wrappers_v4 import SharedAccountEntryLifecycleMixin
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v37_smt_session_divergence import SmtSessionDivergenceStrategy
from strategy_v38_isolated_smt_reversal import IsolatedSmtReversalStrategy


class StrategyV38ContractTest(unittest.TestCase):
    def test_v38_preserves_v26_risk_and_nautilus_execution_inheritance(self) -> None:
        self.assertTrue(issubclass(IsolatedSmtReversalStrategy, SmtSessionDivergenceStrategy))
        self.assertTrue(issubclass(IsolatedSmtReversalStrategy, ScenarioValidEntryStrategy))
        names = set(IsolatedSmtReversalStrategy.__dict__)
        self.assertNotIn("_equity_value", names)
        self.assertNotIn("risk_fraction", names)
        self.assertNotIn("floor_quantity", names)
        self.assertNotIn("submit_order_list", names)

    def test_all_symbols_resolve_global_lifecycle_before_v38_logic(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
            cls = experimental_shared_strategy_class(WINNER, symbol)
            mro = cls.mro()
            self.assertLess(
                mro.index(SharedAccountEntryLifecycleMixin),
                mro.index(IsolatedSmtReversalStrategy),
            )
            self.assertLess(
                mro.index(IsolatedSmtReversalStrategy),
                mro.index(ScenarioValidEntryStrategy),
            )
            self.assertIn(symbol, experimental_shared_strategy_path(WINNER, symbol))

    def test_price_cap_is_risk_basis_not_slippage_relaxation(self) -> None:
        source = inspect.getsource(IsolatedSmtReversalStrategy._submit_isolated_price_cap)
        self.assertIn("worst_entry_preserving_net_r", source)
        self.assertIn("planned_loss_per_unit", source)
        self.assertIn("adverse_slippage_bps_each_side", source)
        self.assertNotIn("risk_multiplier", source)
        self.assertNotIn("leverage", source)

    def test_docstring_requires_full_isolation_and_no_immediate_fill_assumption(self) -> None:
        source = IsolatedSmtReversalStrategy.__doc__ or ""
        self.assertIn("all three peers", source)
        self.assertIn("does not assume immediate execution", source)
        self.assertIn("price", source)


if __name__ == "__main__":
    unittest.main()
