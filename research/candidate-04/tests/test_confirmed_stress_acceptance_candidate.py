from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "confirmed_stress_acceptance_candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v9_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ConfirmedStressAcceptanceTests(unittest.TestCase):
    def config(self) -> SimpleNamespace:
        return SimpleNamespace(
            trend_structure_minutes=5,
            stress_inventory_quantile_window_minutes=720,
            stress_inventory_quantile_min_periods=240,
            trend_efficiency_60s_max=0.80,
            trend_flow_60s=0.10,
            trend_close_location=0.65,
            parent_cluster_minutes=5,
        )

    def data(self, *, accepted: bool) -> pd.DataFrame:
        index = pd.date_range("2024-01-01", periods=1000, freq="1min", tz="UTC")
        close = 100.0 + 0.20 * np.sin(np.arange(1000) / 2.0)
        if accepted:
            close[-6:] = [100.0, 100.2, 100.4, 100.6, 100.8, 101.0]
        else:
            close[-6:] = [100.0, 101.0, 99.4, 101.1, 99.5, 100.2]
        frame = pd.DataFrame(index=index)
        frame["close"] = close
        frame["open"] = frame["close"] - 0.05
        frame["high"] = frame["close"] + 0.05
        frame["low"] = frame["close"] - 0.15
        frame["eff_60s"] = 0.50
        frame["flow_60s"] = 0.30
        return frame

    def intent(self, *, dual: bool = True) -> object:
        return MODULE.Intent(
            scenario="STRESS_INVENTORY_SHOCK_DISPLACEMENT",
            side=1,
            signal_index=999,
            entry_index=1000,
            stop_level=99.0,
            event_indices=(999,),
            details={
                "oi_transfer": True,
                "execution_shock": dual,
            },
        )

    def test_dual_mechanism_without_acceptance_is_rejected(self) -> None:
        parent = self.intent(dual=True)
        MODULE.filter_dual_mechanism_inventory_intents.original_detector = (
            lambda *_: ([parent], [{"signal_index": 999}])
        )
        intents, diagnostics = MODULE.filter_dual_mechanism_inventory_intents(
            self.data(accepted=False),
            pd.Timestamp("2024-01-01", tz="UTC"),
            pd.Timestamp("2024-01-02", tz="UTC"),
            self.config(),
        )
        self.assertEqual(intents, [])
        self.assertFalse(diagnostics[0]["passed"])

    def test_single_mechanism_is_not_overconstrained(self) -> None:
        parent = self.intent(dual=False)
        MODULE.filter_dual_mechanism_inventory_intents.original_detector = (
            lambda *_: ([parent], [{"signal_index": 999}])
        )
        intents, diagnostics = MODULE.filter_dual_mechanism_inventory_intents(
            self.data(accepted=False),
            pd.Timestamp("2024-01-01", tz="UTC"),
            pd.Timestamp("2024-01-02", tz="UTC"),
            self.config(),
        )
        self.assertEqual(len(intents), 1)
        self.assertTrue(diagnostics[0]["passed"])

    def test_reversal_failure_requires_persistent_acceptance(self) -> None:
        parent = MODULE.Intent(
            scenario="STRESS_REVERSAL_FAILURE_CONTINUATION",
            side=1,
            signal_index=999,
            entry_index=1000,
            stop_level=99.0,
            event_indices=(990, 995),
            details={},
        )
        MODULE.filter_reversal_failure_intents.original_detector = (
            lambda *_: ([parent], [{"trigger_index": 999}])
        )
        rejected, _ = MODULE.filter_reversal_failure_intents(
            self.data(accepted=False),
            [],
            self.config(),
        )
        accepted, diagnostics = MODULE.filter_reversal_failure_intents(
            self.data(accepted=True),
            [],
            self.config(),
        )
        self.assertEqual(rejected, [])
        self.assertEqual(len(accepted), 1)
        self.assertTrue(diagnostics[0]["acceptance_filter_passed"])


if __name__ == "__main__":
    unittest.main()
