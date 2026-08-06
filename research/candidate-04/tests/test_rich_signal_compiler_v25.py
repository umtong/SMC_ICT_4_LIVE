from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "rich_signal_compiler_v25.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v25_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class V25ParentResumptionTests(unittest.TestCase):
    def test_smaller_confirmation_is_resumption(self) -> None:
        self.assertTrue(MODULE.is_non_climactic_resumption(-4.0, 5.0))
        self.assertAlmostEqual(
            MODULE.confirmation_to_shock_ratio(-4.0, 5.0),
            0.8,
        )

    def test_equal_confirmation_is_boundary_resumption(self) -> None:
        self.assertTrue(MODULE.is_non_climactic_resumption(5.0, 5.0))
        self.assertEqual(MODULE.confirmation_to_shock_ratio(5.0, 5.0), 1.0)

    def test_larger_confirmation_is_new_impact(self) -> None:
        self.assertFalse(MODULE.is_non_climactic_resumption(5.1, 5.0))

    def test_invalid_shock_is_rejected(self) -> None:
        self.assertFalse(MODULE.is_non_climactic_resumption(1.0, 0.0))
        self.assertTrue(
            math.isnan(MODULE.confirmation_to_shock_ratio(1.0, 0.0)),
        )


if __name__ == "__main__":
    unittest.main()
