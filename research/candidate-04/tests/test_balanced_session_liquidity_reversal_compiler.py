from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

import pandas as pd

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "balanced_session_liquidity_reversal_compiler.py"
)
SPEC = importlib.util.spec_from_file_location(
    "candidate04_balanced_session_test",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BalancedSessionTests(unittest.TestCase):
    def test_auction_efficiency_separates_rotation_and_direction(self) -> None:
        rotational = pd.DataFrame(
            {
                "open": [100.0] * 5,
                "close": [101.0, 99.0, 101.0, 99.0, 100.0],
            }
        )
        directional = pd.DataFrame(
            {
                "open": [100.0] * 5,
                "close": [101.0, 102.0, 103.0, 104.0, 105.0],
            }
        )
        self.assertLess(
            MODULE.session_auction_efficiency(rotational),
            MODULE.session_auction_efficiency(directional),
        )

    def test_vwap_and_weighted_deviation_are_volume_weighted(self) -> None:
        frame = pd.DataFrame(
            {
                "high": [101.0, 111.0],
                "low": [99.0, 109.0],
                "close": [100.0, 110.0],
                "volume": [9.0, 1.0],
            }
        )
        vwap, mad = MODULE.session_vwap_state(frame)
        self.assertAlmostEqual(vwap, 101.0, places=10)
        self.assertAlmostEqual(mad, 1.8, places=10)

    def test_close_must_remain_inside_realized_value_deviation(self) -> None:
        self.assertTrue(MODULE.close_is_accepted_value(101.0, 100.0, 2.0))
        self.assertFalse(MODULE.close_is_accepted_value(103.0, 100.0, 2.0))
        self.assertFalse(MODULE.close_is_accepted_value(math.nan, 100.0, 2.0))

    def test_inventory_routes_are_separate(self) -> None:
        self.assertEqual(
            MODULE.inventory_route(-0.001),
            MODULE.LIQUIDATION_SCENARIO,
        )
        self.assertEqual(
            MODULE.inventory_route(0.001),
            MODULE.FAILED_INVENTORY_SCENARIO,
        )
        self.assertIsNone(MODULE.inventory_route(0.0))
        self.assertIsNone(MODULE.inventory_route(math.nan))


if __name__ == "__main__":
    unittest.main()
