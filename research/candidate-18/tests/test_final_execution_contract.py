from __future__ import annotations

import ast
from pathlib import Path
import unittest


class FinalExecutionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.path = cls.root / "latency_capped_ioc_strategy.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_effective_adapter_uses_ioc_overlay(self) -> None:
        adapter = (self.root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn(
            "from latency_capped_ioc_strategy import Candidate18Strategy",
            adapter,
        )
        self.assertNotIn("latency_emulated_strategy", adapter)

    def test_parent_is_price_capped_ioc_limit(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_submit_entry"
        )
        text = ast.unparse(method)
        self.assertIn("entry_order_type=OrderType.LIMIT", text)
        self.assertIn("entry_price=", text)
        self.assertIn("time_in_force=TimeInForce.IOC", text)
        self.assertIn("planned_loss_per_unit(entry_limit", text)
        self.assertNotIn("OrderType.MARKET", text)
        self.assertNotIn("emulation_trigger=", text)

    def test_overlay_reuses_router_and_nautilus_order_factory(self) -> None:
        classes = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
        }
        strategy = classes["Candidate18Strategy"]
        self.assertTrue(
            any(
                isinstance(base, ast.Name) and base.id == "_Candidate18Strategy"
                for base in strategy.bases
            )
        )
        for forbidden in ("BacktestEngine", "PortfolioSimulator", "MatchingEngine"):
            self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
