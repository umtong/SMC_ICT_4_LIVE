import inspect
import unittest

import portfolio_strategy as strategy
import run_portfolio


class Candidate44Contracts(unittest.TestCase):
    def test_all_symbols_use_identical_v42_logic(self):
        classes = (
            strategy.SharedAccountV44BTCStrategy,
            strategy.SharedAccountV44ETHStrategy,
            strategy.SharedAccountV44SOLStrategy,
            strategy.SharedAccountV44XRPStrategy,
        )
        for cls in classes:
            self.assertIn("SharedSlotMixin", [base.__name__ for base in cls.__mro__])
            self.assertIn("Candidate16Strategy", [base.__name__ for base in cls.__mro__])
        self.assertEqual(set(strategy.STRATEGY_PATHS), set(run_portfolio.SYMBOLS))

    def test_exact_control_only_toggles_poc_ownership(self):
        configured = inspect.getsource(run_portfolio.prepare_configs)
        self.assertIn('"candidate33_require_stacked_imbalance": False', configured)
        self.assertIn('"candidate42_require_poc_migration"', configured)
        self.assertIn(
            '"candidate42_min_consecutive_outside_poc_bars": 2',
            configured,
        )
        self.assertEqual(
            set(run_portfolio.VARIANTS),
            {"poc-migration", "price-only-control"},
        )

    def test_global_slot_wraps_actual_submit_entry(self):
        source = inspect.getsource(strategy.SharedSlotMixin)
        self.assertIn("def _submit_entry", source)
        self.assertIn("acquire_entry_intent", source)
        self.assertIn("position_opened", source)
        self.assertIn("position_closed", source)

    def test_global_runner_compatibility_reset_is_noop(self):
        self.assertIsNone(strategy.reset_shared_btc_leader_context())


if __name__ == "__main__":
    unittest.main()
