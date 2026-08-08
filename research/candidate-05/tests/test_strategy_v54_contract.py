from __future__ import annotations

import ast
from pathlib import Path
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v54_failed_inventory_acceptance import FailedInventoryAcceptanceStrategy


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "strategy_v54_failed_inventory_acceptance.py"


class FailedInventoryAcceptanceStrategyContractTests(unittest.TestCase):
    def test_v54_is_a_complement_over_unchanged_v46(self) -> None:
        self.assertEqual(
            FailedInventoryAcceptanceStrategy.__bases__,
            (NoPostRetraceBreakawayStrategy,),
        )
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        classes = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        strategy = classes["FailedInventoryAcceptanceStrategy"]
        methods = {
            node.name
            for node in strategy.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(
            methods,
            {
                "__init__",
                "on_bar",
                "_expire_pending",
                "_arm_failed_inventory_acceptance_watch",
                "_advance_failed_inventory_acceptance_watches",
                "_failed_inventory_entry_slot_idle",
                "_submit_failed_inventory_acceptance",
                "_close_failed_inventory_watch",
            },
        )

    def test_only_strict_reversal_failure_can_arm_the_complement(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("strict_external_inventory_confirmed", text)
        self.assertIn("SWEEP_EXTREME_INVALIDATED_BEFORE_CHOCH", text)
        self.assertIn("FAILED_INVENTORY_ACCEPTANCE_CONTINUATION", text)
        self.assertIn("FIRST_DEFENDED_RETEST", text)
        self.assertIn("NO_STILL_LIVE_OPPOSING_LIQUIDITY_TARGET", text)

    def test_no_execution_accounting_or_risk_reimplementation(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "BacktestNode",
            "FillModel",
            "matching_engine",
            "risk_fraction =",
            "starting_nav =",
            "submit_order(",
            "submit_order_list(",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("self._submit_price_capped_bracket", text)
        self.assertIn("planned_loss_per_unit", text)
        self.assertIn("choose_liquidity_target", text)


if __name__ == "__main__":
    unittest.main()
