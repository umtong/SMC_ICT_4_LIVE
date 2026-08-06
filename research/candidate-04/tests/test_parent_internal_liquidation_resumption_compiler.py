from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "parent_internal_liquidation_resumption_compiler.py"
SPEC = importlib.util.spec_from_file_location("candidate04_parent_internal_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ParentInternalLiquidationTests(unittest.TestCase):
    def test_parent_state_ends_before_shock(self) -> None:
        rows = 482
        index = pd.date_range("2024-01-01", periods=rows, freq="1min", tz="UTC")
        close = [100.0 + value * 0.01 for value in range(rows)]
        data = pd.DataFrame(
            {
                "close": close,
                "high": [value + 0.5 for value in close],
                "low": [value - 0.5 for value in close],
            },
            index=index,
        )
        shock_index = 481
        data.iloc[shock_index, data.columns.get_loc("close")] = 1.0
        parent = MODULE.completed_parent_auction(data, shock_index)
        self.assertIsNotNone(parent)
        assert parent is not None
        self.assertGreater(parent.return_bps, 0.0)
        self.assertNotEqual(parent.end_price, 1.0)

    def test_long_parent_requires_internal_discount_low_pool(self) -> None:
        parent = MODULE.ParentAuction(1, 100.0, 110.0, 1000.0, 112.0, 98.0, 105.0)
        valid = MODULE.v24.PoolTake(500, 1, -1, 1, 102.0, 101.5, 0.2, 30, 0.5, 2)
        premium = MODULE.v24.PoolTake(500, 2, -1, 1, 108.0, 107.5, 0.2, 30, 0.5, 2)
        outside = MODULE.v24.PoolTake(500, 3, -1, 1, 97.0, 96.5, 0.2, 30, 0.5, 2)
        self.assertTrue(MODULE.pool_is_internal_discount_or_premium(valid, parent))
        self.assertFalse(MODULE.pool_is_internal_discount_or_premium(premium, parent))
        self.assertFalse(MODULE.pool_is_internal_discount_or_premium(outside, parent))

    def test_short_parent_requires_internal_premium_high_pool(self) -> None:
        parent = MODULE.ParentAuction(-1, 110.0, 100.0, -909.0, 112.0, 98.0, 105.0)
        valid = MODULE.v24.PoolTake(500, 1, 1, -1, 108.0, 108.5, 0.2, 30, 0.5, 2)
        discount = MODULE.v24.PoolTake(500, 2, 1, -1, 102.0, 102.5, 0.2, 30, 0.5, 2)
        self.assertTrue(MODULE.pool_is_internal_discount_or_premium(valid, parent))
        self.assertFalse(MODULE.pool_is_internal_discount_or_premium(discount, parent))

    def test_confirmation_must_align_and_not_exceed_shock(self) -> None:
        self.assertTrue(MODULE.non_climactic_confirmation(4.0, 1, 5.0))
        self.assertTrue(MODULE.non_climactic_confirmation(-4.0, -1, 5.0))
        self.assertFalse(MODULE.non_climactic_confirmation(5.1, 1, 5.0))
        self.assertFalse(MODULE.non_climactic_confirmation(-4.0, 1, 5.0))


if __name__ == "__main__":
    unittest.main()
