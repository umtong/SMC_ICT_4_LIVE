from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent


class Candidate35ContractTest(unittest.TestCase):
    def test_fixed_universe_and_risk_budget(self) -> None:
        config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(
            config["symbols"],
            ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        )
        self.assertEqual(config["risk_fraction"], 0.03)
        self.assertGreater(config["all_in_cost_bps_each_side"], 0.0)
        self.assertGreater(config["adverse_slippage_bps_each_side"], 0.0)

    def test_strategy_uses_nautilus_and_has_no_custom_engine(self) -> None:
        source = (HERE / "strategy.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        class_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        self.assertIn("Candidate35Strategy", class_names)
        self.assertNotIn("BacktestEngine", class_names)
        self.assertIn("self.order_factory.bracket", source)
        self.assertIn("self.submit_order_list", source)
        self.assertIn("self.portfolio.is_flat", source)

    def test_one_universe_router_call(self) -> None:
        source = (HERE / "strategy.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("winner, decisions = route_universe("), 1)
        self.assertIn("max_open_positions_observed", source)
        self.assertIn("global_position_violations", source)


if __name__ == "__main__":
    unittest.main()
