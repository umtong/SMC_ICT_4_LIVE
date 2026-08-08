from __future__ import annotations

import ast
from pathlib import Path
import unittest


class Candidate17V2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = Path(__file__).resolve().parents[1] / "remembered_defense_strategy_v2.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_v2_reuses_v1_and_verified_execution_path(self) -> None:
        classes = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
        }
        strategy = classes["Candidate17V2Strategy"]
        self.assertTrue(
            any(
                isinstance(base, ast.Name) and base.id == "Candidate17V1Strategy"
                for base in strategy.bases
            )
        )

    def test_initiative_arms_retest_without_same_bar_entry(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_failure_initiative"
        )
        text = ast.unparse(method)
        self.assertIn("branch='FAILURE_RETEST'", text)
        self.assertNotIn("self._submit_entry", text)

    def test_only_confirmed_retest_can_submit(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_failure_retest"
        )
        text = ast.unparse(method)
        self.assertIn("RetestDecision.CONFIRMED", self.source)
        self.assertIn("self._submit_entry(completed, row)", text)

    def test_geometry_rule_is_economic_not_pnl_fitted(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_submit_entry"
        )
        text = ast.unparse(method)
        self.assertIn("structural_price_risk < execution_cost_component", text)
        for forbidden in ("profit_factor", "win_rate", "daily_growth", "realized_pnl"):
            self.assertNotIn(forbidden, self.source)

    def test_no_new_engine_or_accounting_layer(self) -> None:
        forbidden_imports = {"backtest", "nautilus_trader", "pandas", "numpy"}
        imported: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint(forbidden_imports), imported)


if __name__ == "__main__":
    unittest.main()
