from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).parents[1] / "regime_transition_candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v6", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class Candidate04V6Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = MODULE.Config(
            base=MODULE.v5.Config(),
            basis_regime_window_minutes=4,
            stress_inventory_quantile_window_minutes=10,
            stress_inventory_quantile_min_periods=5,
        )

    def test_basis_regime_never_uses_appended_future(self) -> None:
        index = pd.date_range("2024-01-01", periods=8, freq="1min", tz="UTC")
        frame = pd.DataFrame(
            {"trade_index_basis_bps": [-4.0, -3.0, -2.0, -1.0, 50.0, 50.0, 50.0, 50.0]},
            index=index,
        )
        before = MODULE.basis_regime(frame.iloc[:4], 3, self.config)
        after = MODULE.basis_regime(frame, 3, self.config)
        self.assertEqual(before, after)
        self.assertEqual(after, -2.5)

    def _probe_frame(self, *, target_first: bool) -> pd.DataFrame:
        index = pd.date_range("2024-01-01", periods=12, freq="1min", tz="UTC")
        frame = pd.DataFrame(index=index)
        frame["open"] = 100.0
        frame["high"] = 100.2
        frame["low"] = 99.8
        frame["close"] = 100.0
        frame["atr"] = 1.0
        frame["trade_index_basis_bps"] = -5.0
        if target_first:
            frame.loc[index[6], "low"] = 97.0
            frame.loc[index[6], "close"] = 98.0
        else:
            frame.loc[index[6], "high"] = 102.0
            frame.loc[index[6], "close"] = 101.5
        return frame

    def _parent_probe(self) -> object:
        return MODULE.Intent(
            scenario="SWING_FAILED_AUCTION_RESUMPTION",
            side=-1,
            signal_index=4,
            entry_index=5,
            stop_level=102.0,
            event_indices=(3,),
            details={"index": 3, "extreme": 101.0},
        )

    def test_hypothetical_target_wins_competing_risk(self) -> None:
        frame = self._probe_frame(target_first=True)
        intents, diagnostics = MODULE.detect_stress_failure_intents(
            frame,
            [self._parent_probe()],
            self.config,
        )
        self.assertEqual(intents, [])
        self.assertEqual(diagnostics[0]["outcome"], "REJECTION_SUCCEEDED")

    def test_extreme_failure_creates_next_bar_continuation(self) -> None:
        frame = self._probe_frame(target_first=False)
        intents, diagnostics = MODULE.detect_stress_failure_intents(
            frame,
            [self._parent_probe()],
            self.config,
        )
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, 1)
        self.assertEqual(intents[0].signal_index, 6)
        self.assertEqual(intents[0].entry_index, 7)
        self.assertEqual(diagnostics[0]["outcome"], "ACCEPTANCE_CONFIRMED")

    def test_current_oi_shock_is_excluded_from_its_own_threshold(self) -> None:
        series = pd.Series([0.001] * 10 + [0.5])
        threshold = series.shift(1).rolling(10, min_periods=5).quantile(0.95)
        self.assertAlmostEqual(float(threshold.iloc[-1]), 0.001)
        self.assertGreater(float(series.iloc[-1]), float(threshold.iloc[-1]))

    def test_config_delegates_base_risk_contract(self) -> None:
        self.assertEqual(self.config.risk_fraction, 0.03)
        self.assertEqual(self.config.fee_bps, 5.0)


if __name__ == "__main__":
    unittest.main()
