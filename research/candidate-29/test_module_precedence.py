from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest

import run_continuous


class Candidate29ModulePrecedenceTest(unittest.TestCase):
    def test_candidate16_strategy_v2_precedes_candidate05_name_collision(self) -> None:
        sys.modules.pop("strategy_v2", None)
        module = importlib.import_module("strategy_v2")
        resolved = Path(module.__file__).resolve()
        self.assertEqual(resolved.parent.name, "candidate-16")
        self.assertTrue(hasattr(module, "Candidate16V2Config"))

    def test_exact_candidate_root_order_is_stable(self) -> None:
        actual = [Path(value).resolve() for value in sys.path[:6]]
        expected = [path.resolve() for path in run_continuous._MODULE_PRECEDENCE]
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
