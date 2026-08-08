from __future__ import annotations

import importlib
from pathlib import Path
import unittest


class Candidate17ImportAdapterTests(unittest.TestCase):
    def test_parent_strategy_module_is_not_shadowed(self) -> None:
        adapter = importlib.import_module("candidate17_strategy")
        parent_strategy = importlib.import_module("strategy")
        parent_v2 = importlib.import_module("strategy_v2")

        self.assertEqual(Path(parent_strategy.__file__).resolve().parent.name, "candidate-16")
        self.assertTrue(
            issubclass(adapter.Candidate17Config, parent_v2.Candidate16V2Config)
        )
        self.assertTrue(
            issubclass(adapter.Candidate17Strategy, parent_v2.Candidate16V2Strategy)
        )


if __name__ == "__main__":
    unittest.main()
