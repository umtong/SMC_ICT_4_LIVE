from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "rich_signal_compiler_v26b.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v26b_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class V26bCausalInventoryTests(unittest.TestCase):
    def test_interval_oi_uses_acceptance_and_retest_endpoints(self) -> None:
        oi = pd.Series([100.0, 110.0, 110.0, 121.0])
        self.assertAlmostEqual(
            MODULE.interval_open_interest_change(oi, 1, 3),
            0.10,
            places=10,
        )

    def test_unchanged_oi_is_not_fresh_retest_inventory(self) -> None:
        oi = pd.Series([100.0, 100.0, 100.0])
        change = MODULE.interval_open_interest_change(oi, 1, 2)
        self.assertEqual(change, 0.0)
        self.assertFalse(
            MODULE.is_causal_trapped_countertrend_inventory(
                change,
                -1,
                -2.0,
                -20.0,
            )
        )

    def test_positive_interval_oi_requires_basis_and_parent_alignment(self) -> None:
        self.assertTrue(
            MODULE.is_causal_trapped_countertrend_inventory(
                0.001,
                -1,
                -2.0,
                -20.0,
            )
        )
        self.assertFalse(
            MODULE.is_causal_trapped_countertrend_inventory(
                0.001,
                -1,
                2.0,
                -20.0,
            )
        )
        self.assertFalse(
            MODULE.is_causal_trapped_countertrend_inventory(
                0.001,
                -1,
                -2.0,
                20.0,
            )
        )

    def test_invalid_interval_or_nonfinite_values_are_rejected(self) -> None:
        oi = pd.Series([100.0, 101.0])
        self.assertTrue(
            math.isnan(MODULE.interval_open_interest_change(oi, 1, 1))
        )
        self.assertFalse(
            MODULE.is_causal_trapped_countertrend_inventory(
                math.nan,
                1,
                2.0,
                20.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
