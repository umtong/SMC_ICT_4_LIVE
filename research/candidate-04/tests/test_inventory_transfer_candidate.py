from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).parents[1] / "inventory_transfer_candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v7_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class Candidate04V7Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_detect = MODULE.v6.detect_trend_intents
        self.original_basis = MODULE.v6.basis_regime
        self.index = pd.date_range("2024-01-01", periods=8, freq="1min", tz="UTC")
        self.config = MODULE.Config(
            stress_inventory_quantile_window_minutes=5,
            stress_inventory_quantile_min_periods=3,
            stress_inventory_quantile=0.8,
        )
        MODULE.v6.basis_regime = lambda data, index, config: -1.0

    def tearDown(self) -> None:
        MODULE.v6.detect_trend_intents = self.original_detect
        MODULE.v6.basis_regime = self.original_basis

    def _run(self, *, side: int, oi_values: list[float], burst_values: list[float]):
        parent = MODULE.Intent(
            scenario="INVENTORY_BACKED_DISPLACEMENT",
            side=side,
            signal_index=6,
            entry_index=7,
            stop_level=99.0 if side == 1 else 101.0,
            event_indices=(6,),
            details={"base_state": True},
        )
        MODULE.v6.detect_trend_intents = (
            lambda data, start, end, config: ([parent], [])
        )
        data = pd.DataFrame(
            {
                "oi_change_xday_15m": oi_values,
                "notional_burst_xday_60s": burst_values,
            },
            index=self.index,
        )
        return MODULE.detect_stress_inventory_transfer_intents(
            data,
            self.index[0],
            self.index[-1],
            self.config,
        )

    def test_long_oi_creation_alone_confirms_transfer(self) -> None:
        intents, diagnostics = self._run(
            side=1,
            oi_values=[0.001] * 6 + [0.010, 0.0],
            burst_values=[2.0] * 8,
        )
        self.assertEqual(len(intents), 1)
        self.assertTrue(diagnostics[0]["oi_transfer"])
        self.assertFalse(diagnostics[0]["execution_shock"])

    def test_short_oi_contraction_is_directional_transfer(self) -> None:
        intents, diagnostics = self._run(
            side=-1,
            oi_values=[-0.001] * 6 + [-0.010, 0.0],
            burst_values=[2.0] * 8,
        )
        self.assertEqual(len(intents), 1)
        self.assertGreater(diagnostics[0]["directional_oi_change_15m"], 0.0)
        self.assertTrue(diagnostics[0]["oi_transfer"])

    def test_execution_shock_alone_confirms_transfer(self) -> None:
        intents, diagnostics = self._run(
            side=1,
            oi_values=[0.001] * 8,
            burst_values=[2.0] * 6 + [8.0, 2.0],
        )
        self.assertEqual(len(intents), 1)
        self.assertFalse(diagnostics[0]["oi_transfer"])
        self.assertTrue(diagnostics[0]["execution_shock"])

    def test_neither_mechanism_rejects_signal(self) -> None:
        intents, diagnostics = self._run(
            side=1,
            oi_values=[0.001] * 8,
            burst_values=[2.0] * 8,
        )
        self.assertEqual(intents, [])
        self.assertFalse(diagnostics[0]["passed"])

    def test_current_shock_is_excluded_from_its_own_quantile(self) -> None:
        values = pd.Series([0.001] * 6 + [0.5])
        threshold = values.shift(1).rolling(5, min_periods=3).quantile(0.8)
        self.assertAlmostEqual(float(threshold.iloc[-1]), 0.001)


if __name__ == "__main__":
    unittest.main()
