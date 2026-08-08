from __future__ import annotations

import ast
from pathlib import Path
import unittest


class ManagedProtectionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "managed_protection_ioc_strategy.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_effective_adapter_uses_v4(self) -> None:
        adapter = (self.root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn("managed_protection_ioc_strategy", adapter)
        self.assertNotIn("partial_oto_ioc_strategy", adapter)

    def test_entry_is_standalone_capped_ioc(self) -> None:
        method = next(n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name == "_submit_entry")
        text = ast.unparse(method)
        self.assertIn("self.order_factory.limit", text)
        self.assertIn("time_in_force=TimeInForce.IOC", text)
        self.assertIn("CANDIDATE18_MANAGED_IOC_ENTRY", text)
        self.assertNotIn("order_factory.bracket", text)

    def test_each_fill_creates_independent_reduce_only_protection(self) -> None:
        text = ast.unparse(self.tree)
        self.assertIn("def on_order_filled", text)
        self.assertIn("self.order_factory.stop_market", text)
        self.assertIn("reduce_only=True", text)
        self.assertIn("CANDIDATE18_MANAGED_STOP", text)
        self.assertIn("CANDIDATE18_MANAGED_TARGET", text)
        self.assertNotIn("oto_trigger_mode", text)

    def test_no_custom_engine(self) -> None:
        for forbidden in ("BacktestEngine", "PortfolioSimulator", "MatchingEngine"):
            self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
