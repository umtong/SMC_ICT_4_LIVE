from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "rich_signal_compiler_v26.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v26_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class V26InventoryStateTests(unittest.TestCase):
    def test_completed_return_uses_only_trailing_completed_prices(self) -> None:
        close = pd.Series([100.0, 101.0, 102.0, 104.0])
        result = MODULE.completed_return_bps(close, 3, bars=3)
        self.assertAlmostEqual(result, 400.0, places=10)

    def test_fresh_countertrend_inventory_requires_all_three_relations(self) -> None:
        self.assertTrue(
            MODULE.is_trapped_countertrend_inventory(
                0.001,
                -1,
                -3.0,
                -25.0,
            )
        )
        self.assertTrue(
            MODULE.is_trapped_countertrend_inventory(
                0.001,
                1,
                2.0,
                10.0,
            )
        )

    def test_oi_contraction_is_not_fresh_countertrend_inventory(self) -> None:
        self.assertFalse(
            MODULE.is_trapped_countertrend_inventory(
                -0.001,
                -1,
                -3.0,
                -25.0,
            )
        )

    def test_basis_or_parent_misalignment_rejects_state(self) -> None:
        self.assertFalse(
            MODULE.is_trapped_countertrend_inventory(
                0.001,
                1,
                -2.0,
                10.0,
            )
        )
        self.assertFalse(
            MODULE.is_trapped_countertrend_inventory(
                0.001,
                -1,
                -2.0,
                10.0,
            )
        )

    def test_nonfinite_values_and_invalid_side_are_rejected(self) -> None:
        self.assertFalse(
            MODULE.is_trapped_countertrend_inventory(
                math.nan,
                -1,
                -2.0,
                -10.0,
            )
        )
        self.assertFalse(
            MODULE.is_trapped_countertrend_inventory(
                0.001,
                0,
                -2.0,
                -10.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
