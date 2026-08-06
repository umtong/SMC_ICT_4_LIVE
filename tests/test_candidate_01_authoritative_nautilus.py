from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
OFFICIAL = (
    CANDIDATE / "intrinsic_external_liquidity_v4_nautilus_week.py",
    CANDIDATE / "intrinsic_external_liquidity_v4_nautilus_period.py",
)


class AuthoritativeNautilusContractTests(unittest.TestCase):
    def test_official_runners_have_no_custom_simulator(self) -> None:
        for path in OFFICIAL:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            self.assertNotIn("simulate(", source)
            self.assertIn("run_nautilus_plan_backtest", source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = {alias.name for alias in node.names}
                    self.assertNotIn("simulate", names)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertNotEqual(node.name, "simulate")

    def test_adapter_declares_nautilus_as_execution_owner(self) -> None:
        path = CANDIDATE / "nautilus_plan_backtest.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn("run_nautilus_plan_backtest", functions)
        self.assertNotIn("simulate", functions)
        self.assertIn("BacktestEngine", source)
        self.assertIn('"custom_fill_simulator": False', source)
        self.assertIn('"custom_pnl_or_nav_ledger": False', source)

    def test_official_risk_is_fixed_at_three_percent(self) -> None:
        import json

        payload = json.loads(
            (CANDIDATE / "nautilus_execution.json").read_text(
                encoding="utf-8",
            ),
        )
        self.assertEqual(payload["risk_fraction"], 0.03)
        self.assertEqual(payload["all_in_cost_bps_per_side"], 7.0)


if __name__ == "__main__":
    unittest.main()
