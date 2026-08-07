from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "strategy_v46_no_post_retrace_breakaway.py"


class NoPostRetraceBreakawayContractTests(unittest.TestCase):
    def test_subclasses_v45_and_changes_only_entry_path_resolution(self) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        strategy = classes["NoPostRetraceBreakawayStrategy"]
        self.assertEqual(
            [ast.unparse(base) for base in strategy.bases],
            ["ActiveExternalInventoryStrategy"],
        )
        methods = {
            node.name
            for node in strategy.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(methods, {"__init__", "_resolve_entry_path"})

    def test_preserves_parent_response_path_and_expires_only_state_contradiction(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("super()._resolve_entry_path(row)", text)
        self.assertIn("no_retrace_breakaway_allowed(", text)
        self.assertIn('"NO_RETRACE_BREAKAWAY_INVALID_AFTER_CHOCH_RETEST"', text)
        self.assertIn('armed.details.get("retest_touch_count", 0)', text)
        for forbidden in (
            "BacktestNode",
            "submit_order(",
            "submit_order_list(",
            "risk_fraction =",
            "planned_loss_per_unit",
            "target_price =",
            "stop_price =",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
