from __future__ import annotations

import ast
from pathlib import Path
import unittest

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
from shared_account_strategy_variants_v2 import final_shared_strategy_class
from shared_account_strategy_variants_v2 import final_shared_strategy_class_name
from shared_account_strategy_variants_v2 import final_shared_strategy_path
from strategy_global_slot_wrappers_v4 import SharedAccountEntryLifecycleMixin
import strategy_global_slot_wrappers_v4 as wrappers_v4
from strategy_global_slot_wrappers_v7 import FinalSharedAccountV46Strategy
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "strategy_global_slot_wrappers_v7.py"
WINNER = "strategy_v46_no_post_retrace_breakaway:NoPostRetraceBreakawayStrategy"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class SharedAccountV46ContractTests(unittest.TestCase):
    def test_wrapper_combines_only_slot_lifecycle_and_unchanged_v46_logic(self) -> None:
        self.assertEqual(
            FinalSharedAccountV46Strategy.__bases__,
            (SharedAccountEntryLifecycleMixin, NoPostRetraceBreakawayStrategy),
        )
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual([node.name for node in classes], ["FinalSharedAccountV46Strategy"])
        self.assertFalse(
            any(
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                for item in classes[0].body
            ),
        )

    def test_wrapper_uses_the_final_strict_release_coordinator(self) -> None:
        self.assertIs(
            wrappers_v4.SHARED_ACCOUNT_ENTRY_COORDINATOR,
            FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR,
        )

    def test_every_project_symbol_has_a_distinct_importable_v46_class(self) -> None:
        names = {final_shared_strategy_class_name(WINNER, symbol) for symbol in SYMBOLS}
        self.assertEqual(len(names), 4)
        for symbol in SYMBOLS:
            cls = final_shared_strategy_class(WINNER, symbol)
            self.assertEqual(cls.__name__, final_shared_strategy_class_name(WINNER, symbol))
            self.assertTrue(issubclass(cls, FinalSharedAccountV46Strategy))
            self.assertEqual(
                final_shared_strategy_path(WINNER, symbol),
                f"shared_account_strategy_variants_v2:{cls.__name__}",
            )

    def test_wrapper_contains_no_execution_or_risk_reimplementation(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "BacktestNode",
            "FillModel",
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
