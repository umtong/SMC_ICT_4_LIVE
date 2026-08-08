import inspect
import unittest

import strategy


class Candidate38Contracts(unittest.TestCase):
    def test_exact_market_control_exists(self):
        source = inspect.getsource(strategy.Candidate16Strategy._submit_entry)
        self.assertIn("candidate38_use_stack_limit", source)
        self.assertIn("super()._submit_entry(setup, row)", source)

    def test_limit_uses_observed_stack_and_gtd(self):
        source = inspect.getsource(strategy.Candidate16Strategy)
        self.assertIn("candidate33_stack_poc", source)
        self.assertIn("entry_order_type=OrderType.LIMIT", source)
        self.assertIn("time_in_force=TimeInForce.GTD", source)
        self.assertIn("entry_post_only=True", source)

    def test_risk_and_natural_target_remain_explicit(self):
        source = inspect.getsource(strategy.Candidate16Strategy._submit_entry)
        self.assertIn("self.config.risk_fraction", source)
        self.assertIn("NO_UNCONSUMED_LIQUIDITY_OBJECTIVE", source)
        self.assertIn("planned_loss_per_unit", source)


if __name__ == "__main__":
    unittest.main()
