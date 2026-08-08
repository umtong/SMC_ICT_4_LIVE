from __future__ import annotations

import inspect
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v74_structural_second_touch import StructuralSecondTouchStrategy


class StructuralSecondTouchContractTests(unittest.TestCase):
    def test_v74_changes_only_response_entry_timing_over_v46(self) -> None:
        self.assertTrue(
            issubclass(
                StructuralSecondTouchStrategy,
                NoPostRetraceBreakawayStrategy,
            ),
        )
        changed = {
            name
            for name, value in StructuralSecondTouchStrategy.__dict__.items()
            if callable(value) and name.startswith("_") and not name.startswith("__")
        }
        self.assertEqual(changed, {"_submit_retest_response"})

    def test_response_close_is_never_used_as_entry(self) -> None:
        source = inspect.getsource(
            StructuralSecondTouchStrategy._submit_retest_response,
        )
        self.assertIn("entry_price = self.instrument.make_price(armed.choch_close)", source)
        self.assertIn("confirmed_second_touch_geometry", source)
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertNotIn("slippage_protected_marketable_limit", source)
        self.assertNotIn("super()._submit_retest_response", source)

    def test_stop_target_and_risk_are_inherited_frozen_geometry(self) -> None:
        source = inspect.getsource(
            StructuralSecondTouchStrategy._submit_retest_response,
        )
        self.assertIn("self._frozen_target_price(armed)", source)
        self.assertIn("stop_price = self.instrument.make_price(armed.stop)", source)
        self.assertIn("planned_loss=geometry.planned_loss_per_unit", source)
        self.assertIn("branch=self.SECOND_TOUCH_BRANCH", source)


if __name__ == "__main__":
    unittest.main()
