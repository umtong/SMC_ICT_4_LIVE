from __future__ import annotations

import ast
from pathlib import Path
import unittest

from strategy_v9 import ArmedEntryPath


class StrategyV29bContractTest(unittest.TestCase):
    def test_armed_entry_path_call_matches_current_dataclass_fields(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "strategy_v29b_external_displacement_fvg.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ArmedEntryPath"
        ]
        self.assertEqual(len(calls), 1)
        keywords = {item.arg for item in calls[0].keywords}
        expected = set(ArmedEntryPath.__dataclass_fields__)
        self.assertEqual(keywords, expected)
        self.assertIn("created_ts", keywords)
        self.assertNotIn("expires_index", keywords)


if __name__ == "__main__":
    unittest.main()
