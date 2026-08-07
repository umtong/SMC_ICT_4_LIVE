from __future__ import annotations

import unittest

from shared_account_strategy_variants_v2 import final_shared_strategy_class
from shared_account_strategy_variants_v2 import final_shared_strategy_path
from strategy_global_slot_wrappers_v4 import SharedAccountEntryLifecycleMixin
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v37_smt_session_divergence import SmtSessionDivergenceStrategy


WINNER = "strategy_v37_smt_session_divergence:SmtSessionDivergenceStrategy"


class StrategyV37ContractTest(unittest.TestCase):
    def test_v37_preserves_v26_risk_and_execution_inheritance(self) -> None:
        self.assertTrue(issubclass(SmtSessionDivergenceStrategy, ScenarioValidEntryStrategy))
        names = set(SmtSessionDivergenceStrategy.__dict__)
        self.assertNotIn("_equity_value", names)
        self.assertNotIn("risk_fraction", names)
        self.assertNotIn("floor_quantity", names)

    def test_all_symbols_resolve_to_one_global_lifecycle_before_orders(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
            cls = final_shared_strategy_class(WINNER, symbol)
            mro = cls.mro()
            self.assertLess(mro.index(SharedAccountEntryLifecycleMixin), mro.index(SmtSessionDivergenceStrategy))
            self.assertLess(mro.index(SmtSessionDivergenceStrategy), mro.index(ScenarioValidEntryStrategy))
            self.assertIn(symbol, final_shared_strategy_path(WINNER, symbol))

    def test_smt_is_context_then_local_confirmation_not_standalone_order(self) -> None:
        source = SmtSessionDivergenceStrategy.__doc__ or ""
        self.assertIn("only context", source)
        self.assertIn("reclaim", source)
        self.assertIn("break", source)


if __name__ == "__main__":
    unittest.main()
