from __future__ import annotations

import ast
from pathlib import Path
import unittest


class StrategyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = Path(__file__).resolve().parents[1] / "remembered_defense_strategy.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_reuses_candidate16_verified_execution_owner(self) -> None:
        classes = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
        }
        strategy = classes["Candidate17Strategy"]
        self.assertTrue(
            any(
                isinstance(base, ast.Name) and base.id == "Candidate16V2Strategy"
                for base in strategy.bases
            )
        )

    def test_state_roles_use_causal_features_not_pnl(self) -> None:
        required = {
            '"flow_60s"',
            '"ret_60s_bps"',
            '"efficiency_60s"',
            '"depth_imbalance_1"',
            '"oi_change_5m"',
            '"metrics_age_seconds"',
            '"ask_depth_change_2_1m"',
            '"bid_depth_change_2_1m"',
        }
        for token in required:
            self.assertIn(token, self.source)
        for forbidden in ("profit_factor", "win_rate", "daily_growth", "realized_pnl"):
            self.assertNotIn(forbidden, self.source)

    def test_depletion_arms_retest_instead_of_same_bar_entry(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_defense_memory"
        )
        text = ast.unparse(method)
        self.assertIn("branch='ACCEPTANCE'", text)
        self.assertIn("retrace_armed=True", text)
        self.assertNotIn("self._submit_entry", text)

    def test_no_new_backtest_or_accounting_engine(self) -> None:
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
