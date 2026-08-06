from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "rich_signal_compiler_v24.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v24_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class V24ImpactSplitTests(unittest.TestCase):
    @staticmethod
    def config(*, minimum_age: int = 3) -> SimpleNamespace:
        return SimpleNamespace(
            pivot_left=1,
            pivot_right=1,
            pool_max_age_minutes=30,
            pool_merge_atr=0.10,
            pool_min_age_minutes=minimum_age,
            pool_min_prominence_atr=0.10,
            sweep_min_atr=0.03,
        )

    @staticmethod
    def frame() -> pd.DataFrame:
        rows = 20
        high = [100.0] * rows
        low = [99.0] * rows
        close = [99.5] * rows
        high[5] = 105.0
        high[10] = 105.2
        high[12] = 105.3
        return pd.DataFrame(
            {
                "high": high,
                "low": low,
                "close": close,
                "atr": [1.0] * rows,
            },
            index=pd.date_range("2024-01-01", periods=rows, freq="1min", tz="UTC"),
        )

    def test_confirmed_pool_is_recorded_only_on_first_eligible_penetration(self) -> None:
        takes = MODULE.detect_external_pool_takes(self.frame(), self.config())
        self.assertIn(10, takes)
        matching = [take for take in takes[10] if take.pool_side == 1]
        self.assertEqual(len(matching), 1)
        self.assertAlmostEqual(matching[0].level, 105.0)
        self.assertEqual(matching[0].trade_side, -1)
        self.assertNotIn(
            12,
            {
                index
                for index, values in takes.items()
                if any(take.pool_id == matching[0].pool_id for take in values)
            },
        )

    def test_too_young_first_penetration_consumes_pool(self) -> None:
        frame = self.frame()
        frame.loc[frame.index[7], "high"] = 105.1
        takes = MODULE.detect_external_pool_takes(
            frame,
            self.config(minimum_age=3),
        )
        old_pool_takes = [
            take
            for values in takes.values()
            for take in values
            if abs(take.level - 105.0) < 1e-9
        ]
        self.assertEqual(old_pool_takes, [])

    def test_pool_reclaim_is_directional(self) -> None:
        take = MODULE.PoolTake(10, 1, 1, -1, 105.0, 105.2, 0.2, 4, 5.0, 1)
        self.assertTrue(MODULE.pool_is_reclaimed(take, 104.9))
        self.assertFalse(MODULE.pool_is_reclaimed(take, 105.1))

    def test_parent_must_dominate_shock_in_trade_direction(self) -> None:
        self.assertTrue(MODULE.parent_dominates_shock(-20.0, -1, 5.0))
        self.assertTrue(MODULE.parent_dominates_shock(20.0, 1, 5.0))
        self.assertFalse(MODULE.parent_dominates_shock(-4.0, -1, 5.0))
        self.assertFalse(MODULE.parent_dominates_shock(20.0, -1, 5.0))


if __name__ == "__main__":
    unittest.main()
