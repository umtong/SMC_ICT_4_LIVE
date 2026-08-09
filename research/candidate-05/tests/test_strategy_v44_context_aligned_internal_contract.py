from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "strategy_v44_context_aligned_internal.py"


class ContextAlignedInternalContractTests(unittest.TestCase):
    def test_changes_only_post_detection_internal_context_gate(self) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        strategy = classes["ContextAlignedInternalStrategy"]
        self.assertEqual(
            [ast.unparse(base) for base in strategy.bases],
            ["TargetResetParticipationStrategy"],
        )
        methods = {
            node.name
            for node in strategy.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(methods, {"__init__", "_detect_sweep"})

    def test_preserves_execution_and_risk_contracts(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("super()._detect_sweep(row, previous_close)", text)
        self.assertIn("INTERNAL_TRAP_REQUIRES_ACCEPTED_ALIGNED_QUARTER_REPRICING", text)
        for forbidden in (
            "BacktestNode",
            "submit_order(",
            "submit_order_list(",
            "risk_fraction =",
            "planned_loss_per_unit",
            "target_price",
            "stop_price",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
