from __future__ import annotations

import ast
from pathlib import Path
import unittest


class LocalTwinTriggerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (
            cls.root / "local_twin_trigger_strategy.py"
        ).read_text(encoding="utf-8")
        cls.adapter = (
            cls.root / "candidate18_strategy.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_effective_adapter_uses_v7(self) -> None:
        self.assertIn("local_twin_trigger_strategy", self.adapter)

    def test_stop_and_target_are_both_local_last_price_triggers(self) -> None:
        method = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_submit_pending_protection"
        )
        text = ast.unparse(method)
        self.assertIn("self.order_factory.stop_market", text)
        self.assertIn("self.order_factory.market_if_touched", text)
        self.assertGreaterEqual(
            text.count("emulation_trigger=TriggerType.LAST_PRICE"),
            2,
        )
        self.assertGreaterEqual(
            text.count("trigger_type=TriggerType.LAST_PRICE"),
            2,
        )
        self.assertGreaterEqual(text.count("reduce_only=True"), 2)
        self.assertIn("CANDIDATE18_V7_LOCAL_TWIN_STOP", text)
        self.assertIn("CANDIDATE18_V7_LOCAL_TWIN_TARGET", text)
        self.assertNotIn("self.order_factory.limit(", text)

    def test_first_release_cancels_only_opposite_local_family(self) -> None:
        release = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "on_order_released"
        )
        text = ast.unparse(release)
        self.assertIn("self._cancel_local_family(self._v5_stop_ids)", text)
        self.assertIn("self._cancel_local_family(self._v5_target_ids)", text)
        self.assertIn("ClientOrderId(identifier)", self.source)
        self.assertNotIn("ClientOrderId.from_str", self.source)

    def test_target_fill_wave_does_not_rearm_duplicate_protection(self) -> None:
        filled = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "on_order_filled"
        )
        text = ast.unparse(filled)
        self.assertIn("self._managed_open_qty", text)
        self.assertNotIn("self._submit_pending_protection", text)

    def test_no_custom_matching_or_accounting_engine(self) -> None:
        for forbidden in (
            "BacktestEngine",
            "MatchingEngine",
            "PortfolioSimulator",
            "AccountEngine",
        ):
            self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
