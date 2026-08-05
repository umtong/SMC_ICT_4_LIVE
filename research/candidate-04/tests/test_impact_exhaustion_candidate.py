from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
import unittest

import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "impact_exhaustion_candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v10_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ImpactExhaustionTests(unittest.TestCase):
    def parameters(self) -> MODULE.ImpactParameters:
        return MODULE.ImpactParameters(
            flow_quantile=0.80,
            maximum_absolute_flow=0.80,
            notional_burst_quantile=0.90,
            absolute_return_quantile=0.70,
            minimum_efficiency_60s=0.65,
            confirmation_minutes=3,
            cooldown_minutes=5,
            stop_buffer_atr=0.08,
            quantile_window_minutes=720,
            quantile_min_periods=240,
            target_net_r=2.0,
            maximum_hold_minutes=180,
        )

    def frame(self, event_flow: float = 0.70) -> pd.DataFrame:
        rows = 1000
        index = pd.date_range("2024-01-01", periods=rows, freq="1min", tz="UTC")
        phase = np.arange(rows)
        close = 100.0 + 0.01 * np.sin(phase / 7.0)
        frame = pd.DataFrame(index=index)
        frame["open"] = close
        frame["high"] = close + 0.05
        frame["low"] = close - 0.05
        frame["close"] = close
        frame["atr"] = 0.20
        frame["flow_60s"] = 0.15 + 0.02 * np.sin(phase / 3.0)
        frame["notional_burst_xday_60s"] = 1.0 + 0.05 * np.cos(phase / 5.0)
        frame["eff_60s"] = 0.30
        frame["ret_60s_bps"] = 0.50 * np.sin(phase / 4.0)

        shock = 900
        frame.iloc[shock, frame.columns.get_loc("open")] = 100.0
        frame.iloc[shock, frame.columns.get_loc("high")] = 100.9
        frame.iloc[shock, frame.columns.get_loc("low")] = 99.9
        frame.iloc[shock, frame.columns.get_loc("close")] = 100.8
        frame.iloc[shock, frame.columns.get_loc("flow_60s")] = event_flow
        frame.iloc[shock, frame.columns.get_loc("notional_burst_xday_60s")] = 5.0
        frame.iloc[shock, frame.columns.get_loc("eff_60s")] = 0.85
        frame.iloc[shock, frame.columns.get_loc("ret_60s_bps")] = 8.0

        # Opposite close through the shock origin after all three minutes close.
        frame.iloc[901, frame.columns.get_loc("close")] = 100.2
        frame.iloc[902, frame.columns.get_loc("close")] = 99.9
        frame.iloc[902, frame.columns.get_loc("low")] = 99.8
        frame.iloc[903, frame.columns.get_loc("open")] = 99.9
        return frame

    def detect(self, frame: pd.DataFrame):
        start = frame.index[800]
        end = frame.index[-1]
        return MODULE.detect_impact_exhaustion_intents(
            frame,
            start,
            end,
            object(),
            self.parameters(),
        )

    def test_failed_price_discovery_enters_after_confirmation(self) -> None:
        frame = self.frame()
        intents, diagnostics = self.detect(frame)
        self.assertEqual(len(intents), 1)
        intent = intents[0]
        self.assertEqual(intent.side, -1)
        self.assertEqual(intent.signal_index, 902)
        self.assertEqual(intent.entry_index, 903)
        self.assertLess(intent.stop_level, 101.0)
        self.assertTrue(
            any(item.get("state") == "FAILED_PRICE_DISCOVERY_CONFIRMED" for item in diagnostics),
        )

    def test_extreme_taker_dominance_is_not_faded(self) -> None:
        intents, diagnostics = self.detect(self.frame(event_flow=0.90))
        self.assertEqual(intents, [])
        self.assertTrue(
            any(item.get("state") == "CASCADE_DOMINANCE_REJECTED" for item in diagnostics),
        )

    def test_appending_future_does_not_change_past_cutoffs(self) -> None:
        frame = self.frame()
        before = MODULE.impact_cutoffs(frame, self.parameters())
        extension_index = pd.date_range(
            frame.index[-1] + pd.Timedelta(minutes=1),
            periods=10,
            freq="1min",
        )
        extension = pd.DataFrame(
            {
                "flow_60s": [0.99] * 10,
                "notional_burst_xday_60s": [100.0] * 10,
                "ret_60s_bps": [100.0] * 10,
            },
            index=extension_index,
        )
        extended = pd.concat([frame, extension], axis=0)
        after = MODULE.impact_cutoffs(extended, self.parameters())
        for name in before:
            pd.testing.assert_series_equal(before[name], after[name].iloc[: len(frame)])


if __name__ == "__main__":
    unittest.main()
