from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "session_inventory_acceptance_compiler.py"
SPEC = importlib.util.spec_from_file_location("candidate04_session_acceptance_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SessionInventoryAcceptanceTests(unittest.TestCase):
    def test_non_climactic_hold_is_relational(self) -> None:
        self.assertTrue(MODULE.non_climactic_hold(4.0, 5.0))
        self.assertTrue(MODULE.non_climactic_hold(5.0, 5.0))
        self.assertFalse(MODULE.non_climactic_hold(5.1, 5.0))
        self.assertFalse(MODULE.non_climactic_hold(-1.0, 5.0))

    @staticmethod
    def frame() -> pd.DataFrame:
        index = pd.date_range("2024-01-01T07:55:00Z", periods=12, freq="1min")
        rows = []
        for _ in index:
            rows.append(
                {
                    "open": 99.5,
                    "high": 100.0,
                    "low": 99.0,
                    "close": 99.5,
                    "atr": 1.0,
                    "flow_60s": 0.0,
                    "ret_60s_bps": 0.0,
                    "metric_oi_change_15m": 0.0,
                    "basis_change_5m": 0.0,
                },
            )
        frame = pd.DataFrame(rows, index=index)
        shock = frame.index.get_loc(pd.Timestamp("2024-01-01T08:01:00Z"))
        frame.iloc[shock, frame.columns.get_loc("high")] = 100.3
        frame.iloc[shock, frame.columns.get_loc("close")] = 100.2
        frame.iloc[shock, frame.columns.get_loc("flow_60s")] = 0.5
        frame.iloc[shock, frame.columns.get_loc("ret_60s_bps")] = 5.0
        frame.iloc[shock, frame.columns.get_loc("metric_oi_change_15m")] = 0.01
        frame.iloc[shock, frame.columns.get_loc("basis_change_5m")] = 2.0
        hold = shock + 1
        frame.iloc[hold, frame.columns.get_loc("high")] = 100.4
        frame.iloc[hold, frame.columns.get_loc("low")] = 100.1
        frame.iloc[hold, frame.columns.get_loc("close")] = 100.3
        frame.iloc[hold, frame.columns.get_loc("flow_60s")] = 0.3
        frame.iloc[hold, frame.columns.get_loc("ret_60s_bps")] = 3.0
        frame.iloc[hold, frame.columns.get_loc("metric_oi_change_15m")] = 0.01
        frame.iloc[hold, frame.columns.get_loc("basis_change_5m")] = 1.0
        return frame

    def test_persistent_new_inventory_emits_long(self) -> None:
        intents, counts = MODULE.detect_session_inventory_acceptance_intents(
            self.frame(),
            pd.Timestamp("2024-01-01T08:00:00Z"),
            pd.Timestamp("2024-01-01T08:06:00Z"),
            SimpleNamespace(sweep_min_atr=0.03),
            SimpleNamespace(stop_buffer_atr=0.08),
        )
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, 1)
        self.assertEqual(intents[0].scenario, MODULE.SCENARIO)
        self.assertEqual(counts["confirmed_acceptance"], 1)

    def test_reclaim_before_confirmation_invalidates(self) -> None:
        frame = self.frame()
        hold = frame.index.get_loc(pd.Timestamp("2024-01-01T08:02:00Z"))
        frame.iloc[hold, frame.columns.get_loc("close")] = 99.8
        intents, counts = MODULE.detect_session_inventory_acceptance_intents(
            frame,
            pd.Timestamp("2024-01-01T08:00:00Z"),
            pd.Timestamp("2024-01-01T08:06:00Z"),
            SimpleNamespace(sweep_min_atr=0.03),
            SimpleNamespace(stop_buffer_atr=0.08),
        )
        self.assertEqual(intents, [])
        self.assertEqual(counts["reclaimed_before_confirmation"], 1)

    def test_no_open_interest_creation_rejects_break(self) -> None:
        frame = self.frame()
        shock = frame.index.get_loc(pd.Timestamp("2024-01-01T08:01:00Z"))
        frame.iloc[shock, frame.columns.get_loc("metric_oi_change_15m")] = -0.01
        intents, counts = MODULE.detect_session_inventory_acceptance_intents(
            frame,
            pd.Timestamp("2024-01-01T08:00:00Z"),
            pd.Timestamp("2024-01-01T08:06:00Z"),
            SimpleNamespace(sweep_min_atr=0.03),
            SimpleNamespace(stop_buffer_atr=0.08),
        )
        self.assertEqual(intents, [])
        self.assertEqual(counts["break_without_inventory_alignment"], 1)


if __name__ == "__main__":
    unittest.main()
