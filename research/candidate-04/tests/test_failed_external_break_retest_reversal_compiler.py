from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "failed_external_break_retest_reversal_compiler.py"
)
SPEC = importlib.util.spec_from_file_location(
    "candidate04_failed_break_retest_reversal_test",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FailedBreakRetestReversalTests(unittest.TestCase):
    def test_failed_long_break_must_close_below_exact_pool(self) -> None:
        self.assertTrue(MODULE.inside_prior_range(99.9, 1, 100.0))
        self.assertFalse(MODULE.inside_prior_range(100.0, 1, 100.0))
        self.assertFalse(MODULE.inside_prior_range(100.1, 1, 100.0))

    def test_failed_short_break_must_close_above_exact_pool(self) -> None:
        self.assertTrue(MODULE.inside_prior_range(100.1, -1, 100.0))
        self.assertFalse(MODULE.inside_prior_range(100.0, -1, 100.0))
        self.assertFalse(MODULE.inside_prior_range(99.9, -1, 100.0))

    def test_failure_is_non_impact_only_when_not_larger_than_retest(self) -> None:
        self.assertTrue(MODULE.non_impact_failure(3.0, 5.0))
        self.assertTrue(MODULE.non_impact_failure(5.0, 5.0))
        self.assertFalse(MODULE.non_impact_failure(5.1, 5.0))
        self.assertFalse(MODULE.non_impact_failure(0.0, 5.0))
        self.assertFalse(MODULE.non_impact_failure(math.nan, 5.0))

    def test_inventory_routes_are_mutually_exclusive(self) -> None:
        self.assertEqual(
            MODULE.inventory_route(-0.001),
            MODULE.LIQUIDATION_SCENARIO,
        )
        self.assertEqual(
            MODULE.inventory_route(0.001),
            MODULE.FRESH_INVENTORY_SCENARIO,
        )
        self.assertIsNone(MODULE.inventory_route(0.0))
        self.assertIsNone(MODULE.inventory_route(math.nan))

    def test_invalid_break_side_is_rejected(self) -> None:
        self.assertFalse(MODULE.inside_prior_range(99.0, 0, 100.0))


if __name__ == "__main__":
    unittest.main()
