from __future__ import annotations

import ast
from pathlib import Path
import unittest

from strategy_v9 import ArmedEntryPath
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v35_sequential_flow_regime import SequentialFlowRegimeStrategy


class StrategyV35ContractTest(unittest.TestCase):
    def test_v35_is_incremental_v26_scenario_not_an_execution_engine(self) -> None:
        self.assertTrue(issubclass(SequentialFlowRegimeStrategy, ScenarioValidEntryStrategy))
        names = set(SequentialFlowRegimeStrategy.__dict__)
        self.assertNotIn("_equity_value", names)
        self.assertNotIn("risk_fraction", names)
        self.assertNotIn("floor_quantity", names)

    def test_entry_path_constructor_matches_current_contract(self) -> None:
        path = Path(__file__).resolve().parents[1] / "strategy_v35_sequential_flow_regime.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ArmedEntryPath"
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            {item.arg for item in calls[0].keywords},
            set(ArmedEntryPath.__dataclass_fields__),
        )

    def test_first_touch_failure_is_terminal_not_selective_reentry(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "strategy_v35_sequential_flow_regime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("FIRST_BOUNDARY_TOUCH_NOT_DEFENDED_BY_FLOW_AND_DEPTH", source)
        self.assertIn("self._close_sequential_watch", source)


if __name__ == "__main__":
    unittest.main()
