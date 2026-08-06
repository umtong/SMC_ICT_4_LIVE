from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "nt_rich_signal_strategy.py"
SPEC = importlib.util.spec_from_file_location("candidate04_rich_execution_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RichSignalEntryGeometryTests(unittest.TestCase):
    def test_long_fill_must_remain_between_stop_and_target(self) -> None:
        self.assertTrue(MODULE.entry_fill_respects_bracket(100.0, 95.0, 110.0, 1))
        self.assertFalse(MODULE.entry_fill_respects_bracket(94.0, 95.0, 110.0, 1))
        self.assertFalse(MODULE.entry_fill_respects_bracket(111.0, 95.0, 110.0, 1))

    def test_short_fill_must_remain_between_target_and_stop(self) -> None:
        self.assertTrue(MODULE.entry_fill_respects_bracket(100.0, 105.0, 90.0, -1))
        self.assertFalse(MODULE.entry_fill_respects_bracket(106.0, 105.0, 90.0, -1))
        self.assertFalse(MODULE.entry_fill_respects_bracket(89.0, 105.0, 90.0, -1))

    def test_nonfinite_and_invalid_side_are_rejected(self) -> None:
        self.assertFalse(MODULE.entry_fill_respects_bracket(float("nan"), 95.0, 110.0, 1))
        self.assertFalse(MODULE.entry_fill_respects_bracket(100.0, 95.0, 110.0, 0))


if __name__ == "__main__":
    unittest.main()
