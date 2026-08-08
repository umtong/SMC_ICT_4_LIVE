from __future__ import annotations

import ast
from pathlib import Path
import unittest


class Candidate19ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = Path(__file__).resolve().parents[1] / "transmission_strategy.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_reuses_candidate18_fok_execution(self) -> None:
        self.assertIn("Candidate18FokStrategy", self.source)
        self.assertIn("class Candidate19Strategy(Candidate18FokStrategy)", self.source)
        self.assertNotIn("TimeInForce", self.source)
        self.assertNotIn("order_factory", self.source)

    def test_shock_arms_later_transmission_without_entry(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_arm_shock_transmission"
        )
        text = ast.unparse(method)
        self.assertIn("branch='SHOCK_TRANSMISSION'", text)
        self.assertNotIn("_submit_entry", text)

    def test_only_confirmed_transmission_submits_shock_entry(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_process_shock_transmission"
        )
        text = ast.unparse(method)
        self.assertIn("ShockDecision.CONFIRMED", self.source)
        self.assertIn("self._submit_entry(completed, row)", text)

    def test_no_pnl_or_new_engine_layer(self) -> None:
        for token in ("profit_factor", "win_rate", "daily_growth", "realized_pnl"):
            self.assertNotIn(token, self.source)
        imported: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(
            imported.isdisjoint({"backtest", "nautilus_trader", "pandas", "numpy"}),
            imported,
        )


if __name__ == "__main__":
    unittest.main()
