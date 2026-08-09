from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "strategy_v40_unfilled_target_handoff.py"


class UnfilledTargetHandoffContractTests(unittest.TestCase):
    def test_subclasses_current_hybrid_and_does_not_create_execution_engine(self) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        strategy = classes["UnfilledTargetHandoffStrategy"]
        self.assertEqual(
            [ast.unparse(base) for base in strategy.bases],
            ["PositioningResetInventoryHybridStrategy"],
        )
        text = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "BacktestNode",
            "FillModel",
            "ParquetDataCatalog",
            "risk_fraction =",
            "submit_order(",
            "submit_order_list(",
        ):
            self.assertNotIn(forbidden, text)

    def test_only_target_completion_arms_the_handoff(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn('TARGET_COMPLETED_REASON = "SCENARIO_TARGET_REACHED_WHILE_ENTRY_RESTING"', text)
        self.assertIn("self._arm_target_watch(pending, row)", text)
        self.assertIn("self._observe_target_watch(row)", text)
        self.assertIn("super()._request_scenario_entry_cancel(row, reason)", text)


if __name__ == "__main__":
    unittest.main()
