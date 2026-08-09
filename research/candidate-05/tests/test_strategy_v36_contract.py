from __future__ import annotations

import unittest

from shared_account_strategy_variants_v2 import final_shared_strategy_class
from shared_account_strategy_variants_v2 import final_shared_strategy_path
from strategy_global_slot_wrappers_v4 import SharedAccountEntryLifecycleMixin
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v36_cross_asset_repricing_gate import SystemicRepricingGateMixin
from strategy_v36_cross_asset_repricing_gate import SystemicRepricingGateStrategy


WINNER = "strategy_v36_cross_asset_repricing_gate:SystemicRepricingGateStrategy"


class StrategyV36ContractTest(unittest.TestCase):
    def test_single_symbol_class_preserves_v26_market_logic(self) -> None:
        self.assertTrue(issubclass(SystemicRepricingGateStrategy, ScenarioValidEntryStrategy))

    def test_all_project_symbols_resolve_to_shared_gate_before_slot_mixin(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
            cls = final_shared_strategy_class(WINNER, symbol)
            mro = cls.mro()
            self.assertLess(mro.index(SystemicRepricingGateMixin), mro.index(SharedAccountEntryLifecycleMixin))
            self.assertLess(mro.index(SharedAccountEntryLifecycleMixin), mro.index(ScenarioValidEntryStrategy))
            self.assertIn(symbol, final_shared_strategy_path(WINNER, symbol))

    def test_gate_is_a_veto_not_a_risk_or_size_multiplier(self) -> None:
        names = set(SystemicRepricingGateMixin.__dict__)
        self.assertNotIn("_equity_value", names)
        self.assertNotIn("risk_fraction", names)
        self.assertNotIn("floor_quantity", names)
        self.assertIn("_submit_price_capped_bracket", names)


if __name__ == "__main__":
    unittest.main()
