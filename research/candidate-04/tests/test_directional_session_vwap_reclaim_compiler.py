from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "directional_session_vwap_reclaim_compiler.py"
)
SPEC = importlib.util.spec_from_file_location(
    "candidate04_directional_session_vwap_test",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DirectionalSessionVWAPTests(unittest.TestCase):
    def test_long_value_acceptance_requires_close_beyond_vwap_mad(self) -> None:
        self.assertTrue(
            MODULE.directional_value_acceptance(103.0, 100.0, 2.0, 1)
        )
        self.assertFalse(
            MODULE.directional_value_acceptance(102.0, 100.0, 2.0, 1)
        )

    def test_short_value_acceptance_is_directional(self) -> None:
        self.assertTrue(
            MODULE.directional_value_acceptance(97.0, 100.0, 2.0, -1)
        )
        self.assertFalse(
            MODULE.directional_value_acceptance(103.0, 100.0, 2.0, -1)
        )

    def test_inventory_routes_are_mutually_exclusive(self) -> None:
        self.assertEqual(
            MODULE.inventory_route(-0.001),
            MODULE.LIQUIDATION_SCENARIO,
        )
        self.assertEqual(
            MODULE.inventory_route(0.001),
            MODULE.TRAPPED_COUNTER_SCENARIO,
        )
        self.assertIsNone(MODULE.inventory_route(0.0))
        self.assertIsNone(MODULE.inventory_route(math.nan))

    def test_invalid_side_and_nonfinite_value_are_rejected(self) -> None:
        self.assertFalse(
            MODULE.directional_value_acceptance(103.0, 100.0, 2.0, 0)
        )
        self.assertFalse(
            MODULE.directional_value_acceptance(math.nan, 100.0, 2.0, 1)
        )


if __name__ == "__main__":
    unittest.main()
