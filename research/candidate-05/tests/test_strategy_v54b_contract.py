from __future__ import annotations

import ast
from pathlib import Path
import unittest

import strategy_v54_failed_inventory_acceptance as v54
from strategy_v54b_no_retest_depth import (
    FailedInventoryAcceptanceNoRetestDepthStrategy,
)
from strategy_v54b_no_retest_depth import _price_flow_only_first_retest_response


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "strategy_v54b_no_retest_depth.py"


class FailedInventoryAcceptanceNoRetestDepthContractTests(unittest.TestCase):
    def test_ablation_subclasses_v54_without_strategy_methods(self) -> None:
        self.assertEqual(
            FailedInventoryAcceptanceNoRetestDepthStrategy.__bases__,
            (v54.FailedInventoryAcceptanceStrategy,),
        )
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        strategy = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "FailedInventoryAcceptanceNoRetestDepthStrategy"
        )
        self.assertFalse(
            any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                for node in strategy.body
            ),
        )

    def test_only_runtime_predicate_changed(self) -> None:
        self.assertIs(
            v54.first_accepted_level_retest_response,
            _price_flow_only_first_retest_response,
        )
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("depth_imbalance=0.0", text)
        self.assertIn("minimum_directional_depth=0.0", text)
        for forbidden in (
            "BacktestNode",
            "FillModel",
            "planned_loss_per_unit",
            "choose_liquidity_target",
            "submit_order(",
            "risk_fraction =",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
