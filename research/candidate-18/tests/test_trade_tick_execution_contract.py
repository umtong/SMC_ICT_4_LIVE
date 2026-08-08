from __future__ import annotations

import ast
from pathlib import Path
import unittest


class TradeTickExecutionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.catalog = (cls.root / "trade_tick_catalog.py").read_text(encoding="utf-8")
        cls.runner = (cls.root / "candidate.py").read_text(encoding="utf-8")
        cls.strategy = (
            cls.root / "trade_tick_emulated_protection_strategy.py"
        ).read_text(encoding="utf-8")
        cls.adapter = (cls.root / "candidate18_strategy.py").read_text(encoding="utf-8")

    def test_raw_aggtrades_become_native_trade_ticks(self) -> None:
        self.assertIn("TradeTickDataWrangler", self.catalog)
        self.assertIn("_agg_reader", self.catalog)
        self.assertIn("ParquetDataCatalog", self.catalog)
        self.assertIn("catalog.write_data", self.catalog)
        for forbidden in ("MatchingEngine", "BacktestEngine", "PortfolioSimulator"):
            self.assertNotIn(forbidden, self.catalog)

    def test_runner_separates_bar_signals_from_trade_execution(self) -> None:
        self.assertIn("data_cls=TradeTick", self.runner)
        self.assertIn('kwargs["bar_execution"] = False', self.runner)
        self.assertIn('kwargs["trade_execution"] = True', self.runner)
        self.assertIn("add_trade_ticks_to_catalog", self.runner)

    def test_stop_is_locally_emulated_from_last_trade(self) -> None:
        tree = ast.parse(self.strategy)
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_submit_pending_protection"
        )
        text = ast.unparse(method)
        self.assertIn("self.order_factory.stop_market", text)
        self.assertIn("trigger_type=TriggerType.LAST_PRICE", text)
        self.assertIn("emulation_trigger=TriggerType.LAST_PRICE", text)
        self.assertIn("reduce_only=True", text)
        self.assertIn("CANDIDATE18_TRADE_TICK_EMULATED_STOP", text)

    def test_v5_is_effective_adapter(self) -> None:
        self.assertIn("trade_tick_emulated_protection_strategy", self.adapter)
        self.assertNotIn("managed_protection_ioc_strategy import", self.adapter)


if __name__ == "__main__":
    unittest.main()
