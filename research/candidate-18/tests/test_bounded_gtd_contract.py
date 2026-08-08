from __future__ import annotations

import ast
from pathlib import Path
import unittest


class BoundedGtdEntryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (
            cls.root / "bounded_gtd_entry_strategy.py"
        ).read_text(encoding="utf-8")
        cls.adapter = (
            cls.root / "candidate18_strategy.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_effective_adapter_uses_v6(self) -> None:
        self.assertIn("bounded_gtd_entry_strategy", self.adapter)
        self.assertNotIn(
            "trade_tick_emulated_protection_strategy import",
            self.adapter,
        )

    def test_entry_is_one_price_capped_gtd_limit(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_submit_entry"
        )
        text = ast.unparse(method)
        self.assertIn("self.order_factory.limit", text)
        self.assertIn("time_in_force=TimeInForce.GTD", text)
        self.assertIn("expire_time=expire_time", text)
        self.assertIn("entry_limit", text)
        self.assertIn("CANDIDATE18_BOUNDED_GTD_ENTRY", text)
        self.assertNotIn("self.order_factory.market", text)
        self.assertNotIn("TimeInForce.IOC", text)

    def test_total_requested_quantity_is_sized_at_worst_cap(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_submit_entry"
        )
        text = ast.unparse(method)
        self.assertIn("planned_loss_per_unit(entry_limit", text)
        self.assertIn("risk_budget / planned_loss", text)
        self.assertIn("planned_account_loss_at_worst_fill", text)

    def test_expiry_and_cancellation_close_unfilled_intent(self) -> None:
        self.assertIn("def on_order_expired", self.source)
        self.assertIn("def on_order_canceled", self.source)
        self.assertIn("BOUNDED_GTD_EXPIRED", self.source)
        self.assertIn("BOUNDED_GTD_CANCELED", self.source)

    def test_no_custom_execution_or_accounting_engine(self) -> None:
        for forbidden in (
            "BacktestEngine",
            "MatchingEngine",
            "PortfolioSimulator",
            "AccountEngine",
        ):
            self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
