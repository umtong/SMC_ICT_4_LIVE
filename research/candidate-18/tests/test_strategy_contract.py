from __future__ import annotations

import ast
from pathlib import Path
import unittest


class StrategyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = Path(__file__).resolve().parents[1] / "execution_preserving_strategy.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_reuses_candidate17_state_and_verified_execution(self) -> None:
        classes = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
        }
        strategy = classes["Candidate18Strategy"]
        self.assertTrue(
            any(
                isinstance(base, ast.Name) and base.id == "Candidate17Strategy"
                for base in strategy.bases
            )
        )

    def test_entry_is_price_capped_stop_limit_not_market(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_submit_entry"
        )
        text = ast.unparse(method)
        self.assertIn("entry_order_type=OrderType.STOP_LIMIT", text)
        self.assertIn("entry_trigger_price=", text)
        self.assertIn("entry_price=", text)
        self.assertIn("planned_loss_per_unit(entry_limit", text)
        self.assertNotIn("OrderType.MARKET", text)

    def test_remembered_defense_closes_without_depletion_branch(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_arm_defense_memory"
        )
        text = ast.unparse(method)
        self.assertIn("REPEATED_DEFENSE_HAS_NO_CAUSAL_DEPLETION_PROOF", text)
        self.assertNotIn("DefenseMemory(", text)
        self.assertNotIn("advance_defense_memory", text)

    def test_policy_uses_causal_market_evidence_not_pnl(self) -> None:
        for required in (
            '"flow_60s"',
            '"ret_60s_bps"',
            '"depth_imbalance_1"',
            '"notional_burst"',
            '"oi_change_5m"',
        ):
            self.assertIn(required, self.source)
        for forbidden in ("profit_factor", "win_rate", "daily_growth", "realized_pnl"):
            self.assertNotIn(forbidden, self.source)

    def test_no_new_backtest_or_accounting_engine(self) -> None:
        forbidden_imports = {"backtest", "pandas", "numpy"}
        imported: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint(forbidden_imports), imported)


if __name__ == "__main__":
    unittest.main()
