from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v56_early_flow_retrace import EarlyFlowFirstRetraceStrategy


class EarlyFlowFirstRetraceContractTests(unittest.TestCase):
    def test_v56_subclasses_frozen_v46(self) -> None:
        self.assertTrue(
            issubclass(
                EarlyFlowFirstRetraceStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )

    def test_only_price_capped_bracket_submission_is_intercepted(self) -> None:
        changed = {
            name
            for name, value in EarlyFlowFirstRetraceStrategy.__dict__.items()
            if callable(value) and not name.startswith("__")
        }
        self.assertEqual(
            changed,
            {"_is_first_retrace_branch", "_submit_price_capped_bracket"},
        )

    def test_second_touch_and_acceptance_are_explicitly_excluded(self) -> None:
        predicate = EarlyFlowFirstRetraceStrategy._is_first_retrace_branch
        self.assertTrue(predicate("TAIL_FLOW_LIQUIDITY_RETRACE"))
        self.assertFalse(predicate("CONFIRMED_SECOND_TOUCH"))
        self.assertFalse(predicate("EXTERNAL_ACCEPTANCE_RETRACE"))
        self.assertFalse(predicate("SPOT_LED_PRICE_DISCOVERY_PULLBACK"))

    def test_order_factory_and_geometry_remain_inherited(self) -> None:
        source = inspect.getsource(
            EarlyFlowFirstRetraceStrategy._submit_price_capped_bracket,
        )
        self.assertIn("super()._submit_price_capped_bracket", source)
        self.assertNotIn("order_factory", source)
        self.assertNotIn("planned_loss", source)
        self.assertNotIn("target_price", source)


if __name__ == "__main__":
    unittest.main()
