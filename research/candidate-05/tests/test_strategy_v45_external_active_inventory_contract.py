from __future__ import annotations

import ast
from pathlib import Path
import unittest

from external_inventory_wiring_logic import external_setup_from_hybrid


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "strategy_v45_external_active_inventory.py"


class ActiveExternalInventoryContractTests(unittest.TestCase):
    def test_is_a_two_method_signal_ablation_over_v44(self) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        strategy = classes["ActiveExternalInventoryStrategy"]
        self.assertEqual(
            [ast.unparse(base) for base in strategy.bases],
            ["ContextAlignedInternalStrategy"],
        )
        methods = {
            node.name
            for node in strategy.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(methods, {"__init__", "_detect_sweep", "_submit_entry"})

    def test_hybrid_detector_routes_only_confirmed_five_minute_setups(self) -> None:
        self.assertTrue(
            external_setup_from_hybrid(
                {
                    "pool_source": "CONFIRMED_5M_SWING",
                },
            ),
        )
        self.assertTrue(
            external_setup_from_hybrid(
                {
                    "pool_source": "CONFIRMED_5M_SWING",
                    "target_handoff": True,
                },
            ),
        )
        self.assertFalse(
            external_setup_from_hybrid(
                {
                    "hybrid_state": "INTERNAL_INVENTORY_TRAP",
                    "pool_source": "CONFIRMED_3M_INTERNAL",
                },
            ),
        )
        self.assertFalse(external_setup_from_hybrid({"pool_source": "UNKNOWN"}))

    def test_uses_existing_inventory_and_active_choch_predicates(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn("inventory_trap_confirmed(", text)
        self.assertIn('state != "ACTIVE_CONFIRMATION"', text)
        self.assertIn("external_setup_from_hybrid(setup.details)", text)
        self.assertIn("super()._detect_sweep(row, previous_close)", text)
        self.assertIn("return super()._submit_entry(setup, row)", text)
        self.assertNotIn(
            'setup.details.get("hybrid_state") != "EXTERNAL_REJECTION_BASELINE"',
            text,
        )
        for forbidden in (
            "BacktestNode",
            "submit_order(",
            "submit_order_list(",
            "risk_fraction =",
            "planned_loss_per_unit",
            "target_price =",
            "stop_price =",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
