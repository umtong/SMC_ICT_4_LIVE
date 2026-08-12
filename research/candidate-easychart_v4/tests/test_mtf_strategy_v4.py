from __future__ import annotations

from types import SimpleNamespace
import unittest

from mtf_strategy_v4 import is_executable_easychart_plan


class EasyChartExecutionRoleTests(unittest.TestCase):
    def test_macro_plan_is_context_evidence_only(self) -> None:
        self.assertFalse(is_executable_easychart_plan(SimpleNamespace(scale_name="MACRO")))

    def test_micro_plan_is_the_actual_entry_layer(self) -> None:
        self.assertTrue(is_executable_easychart_plan(SimpleNamespace(scale_name="MICRO")))

    def test_unclassified_plan_cannot_bypass_the_hierarchy(self) -> None:
        self.assertFalse(is_executable_easychart_plan(SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
