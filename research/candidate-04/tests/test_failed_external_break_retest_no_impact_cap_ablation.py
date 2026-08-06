from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "failed_external_break_retest_no_impact_cap_ablation_compiler.py"
)
SPEC = importlib.util.spec_from_file_location(
    "candidate04_failed_break_retest_no_cap_test",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FailureImpactCapAblationTests(unittest.TestCase):
    def test_any_positive_finite_failure_passes(self) -> None:
        self.assertTrue(MODULE.positive_failure_return(1.0, 0.5))
        self.assertTrue(MODULE.positive_failure_return(10.0, 1.0))

    def test_zero_negative_or_nonfinite_failure_rejected(self) -> None:
        self.assertFalse(MODULE.positive_failure_return(0.0, 1.0))
        self.assertFalse(MODULE.positive_failure_return(-1.0, 1.0))
        self.assertFalse(MODULE.positive_failure_return(math.nan, 1.0))


if __name__ == "__main__":
    unittest.main()
