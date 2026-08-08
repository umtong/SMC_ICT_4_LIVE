from __future__ import annotations

import ast
from pathlib import Path
import unittest


class LatencyEmulationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = Path(__file__).resolve().parents[1] / "latency_emulated_strategy.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_effective_adapter_uses_latency_overlay(self) -> None:
        adapter = (self.path.parent / "candidate18_strategy.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("from latency_emulated_strategy import Candidate18Strategy", adapter)

    def test_stop_limit_is_locally_emulated_and_price_capped(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_submit_entry"
        )
        text = ast.unparse(method)
        self.assertIn("emulation_trigger=TriggerType.DEFAULT", text)
        self.assertIn("entry_order_type=OrderType.STOP_LIMIT", text)
        self.assertIn("entry_trigger_price=", text)
        self.assertIn("entry_price=", text)
        self.assertIn("planned_loss_per_unit(entry_limit", text)

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
        forbidden = ("BacktestEngine", "PortfolioSimulator", "MatchingEngine")
        for name in forbidden:
            self.assertNotIn(name, self.source)


if __name__ == "__main__":
    unittest.main()
