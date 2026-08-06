from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "rich_signal_compiler_v26c.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v26c_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class V26cInventorySemanticsTests(unittest.TestCase):
    def test_raw_oi_growth_confirms_long_or_short_inventory(self) -> None:
        self.assertTrue(
            MODULE.raw_open_interest_confirms_creation(
                {"raw_oi_change_15m": 0.001}
            )
        )

    def test_raw_oi_contraction_is_not_fresh_short_inventory(self) -> None:
        self.assertFalse(
            MODULE.raw_open_interest_confirms_creation(
                {"raw_oi_change_15m": -0.001}
            )
        )

    def test_zero_missing_or_nonfinite_oi_is_rejected(self) -> None:
        self.assertFalse(
            MODULE.raw_open_interest_confirms_creation(
                {"raw_oi_change_15m": 0.0}
            )
        )
        self.assertFalse(MODULE.raw_open_interest_confirms_creation({}))
        self.assertFalse(
            MODULE.raw_open_interest_confirms_creation(
                {"raw_oi_change_15m": math.nan}
            )
        )


if __name__ == "__main__":
    unittest.main()
