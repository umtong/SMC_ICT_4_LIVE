from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "session_liquidity_resiliency_compiler.py"
SPEC = importlib.util.spec_from_file_location("candidate04_session_resiliency_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SessionLiquidityResiliencyTests(unittest.TestCase):
    def test_session_start_uses_utc_eight_hour_boundaries(self) -> None:
        self.assertEqual(
            MODULE.session_start(pd.Timestamp("2024-01-01T07:59:00Z")),
            pd.Timestamp("2024-01-01T00:00:00Z"),
        )
        self.assertEqual(
            MODULE.session_start(pd.Timestamp("2024-01-01T08:00:00Z")),
            pd.Timestamp("2024-01-01T08:00:00Z"),
        )
        self.assertEqual(
            MODULE.session_start(pd.Timestamp("2024-01-01T23:59:00Z")),
            pd.Timestamp("2024-01-01T16:00:00Z"),
        )

    def test_replenishment_direction_is_trade_side_specific(self) -> None:
        row = pd.Series(
            {
                **{f"bid_chg_{band}_60s": 0.10 for band in MODULE.DEPTH_BANDS},
                **{f"ask_chg_{band}_60s": -0.05 for band in MODULE.DEPTH_BANDS},
            },
        )
        self.assertGreater(MODULE.passive_replenishment_differential(row, 1), 0.0)
        self.assertLess(MODULE.passive_replenishment_differential(row, -1), 0.0)

    @staticmethod
    def frame() -> pd.DataFrame:
        index = pd.date_range(
            "2024-01-01T07:55:00Z",
            periods=12,
            freq="1min",
        )
        rows = []
        for _ in index:
            row = {
                "open": 99.5,
                "high": 100.0,
                "low": 99.0,
                "close": 99.5,
                "atr": 1.0,
                "flow_60s": 0.0,
                "ret_60s_bps": 0.0,
                "depth_snapshot_age_seconds": 10.0,
            }
            for band in MODULE.DEPTH_BANDS:
                row[f"bid_chg_{band}_60s"] = 0.0
                row[f"ask_chg_{band}_60s"] = 0.0
            rows.append(row)
        frame = pd.DataFrame(rows, index=index)
        # Previous 00:00-08:00 session high is 100.0. At 08:01 aggressive
        # buying penetrates it, closes back inside, and ask depth replenishes.
        shock = frame.index.get_loc(pd.Timestamp("2024-01-01T08:01:00Z"))
        frame.iloc[shock, frame.columns.get_loc("high")] = 100.2
        frame.iloc[shock, frame.columns.get_loc("close")] = 99.9
        frame.iloc[shock, frame.columns.get_loc("flow_60s")] = 0.4
        frame.iloc[shock, frame.columns.get_loc("ret_60s_bps")] = 2.0
        for band in MODULE.DEPTH_BANDS:
            frame.iloc[shock, frame.columns.get_loc(f"bid_chg_{band}_60s")] = -0.05
            frame.iloc[shock, frame.columns.get_loc(f"ask_chg_{band}_60s")] = 0.10
        return frame

    def test_same_bar_attack_absorption_emits_short_intent(self) -> None:
        frame = self.frame()
        intents, counts = MODULE.detect_session_resiliency_intents(
            frame,
            pd.Timestamp("2024-01-01T08:00:00Z"),
            pd.Timestamp("2024-01-01T08:06:00Z"),
            SimpleNamespace(sweep_min_atr=0.03),
            SimpleNamespace(stop_buffer_atr=0.08),
        )
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, -1)
        self.assertEqual(intents[0].scenario, MODULE.SCENARIO)
        self.assertEqual(
            intents[0].details["confirmation_mode"],
            "SAME_BAR_ATTACK_ABSORPTION",
        )
        self.assertEqual(counts["same_bar_absorption"], 1)

    def test_unaligned_first_attack_is_consumed_and_not_reused(self) -> None:
        frame = self.frame()
        shock = frame.index.get_loc(pd.Timestamp("2024-01-01T08:01:00Z"))
        frame.iloc[shock, frame.columns.get_loc("flow_60s")] = -0.4
        # A later, stronger penetration cannot reuse the consumed boundary.
        later = frame.index.get_loc(pd.Timestamp("2024-01-01T08:03:00Z"))
        frame.iloc[later, frame.columns.get_loc("high")] = 100.5
        frame.iloc[later, frame.columns.get_loc("close")] = 99.8
        frame.iloc[later, frame.columns.get_loc("flow_60s")] = 0.5
        frame.iloc[later, frame.columns.get_loc("ret_60s_bps")] = 3.0
        for band in MODULE.DEPTH_BANDS:
            frame.iloc[later, frame.columns.get_loc(f"ask_chg_{band}_60s")] = 0.2
        intents, counts = MODULE.detect_session_resiliency_intents(
            frame,
            pd.Timestamp("2024-01-01T08:00:00Z"),
            pd.Timestamp("2024-01-01T08:06:00Z"),
            SimpleNamespace(sweep_min_atr=0.03),
            SimpleNamespace(stop_buffer_atr=0.08),
        )
        self.assertEqual(intents, [])
        self.assertEqual(counts["unaligned_attacks"], 1)


if __name__ == "__main__":
    unittest.main()
