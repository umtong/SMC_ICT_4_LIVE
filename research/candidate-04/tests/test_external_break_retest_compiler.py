from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "external_break_retest_compiler.py"
SPEC = importlib.util.spec_from_file_location("candidate04_external_retest_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ExternalBreakRetestTests(unittest.TestCase):
    def test_directional_state_requires_flow_return_and_basis(self) -> None:
        row = pd.Series(
            {"flow_60s": 0.3, "ret_60s_bps": 4.0, "basis_change_5m": 1.0},
        )
        self.assertTrue(MODULE.aligned_acceptance_state(MODULE.directional_state(row, 1)))
        self.assertFalse(MODULE.aligned_acceptance_state(MODULE.directional_state(row, -1)))

    def test_non_climactic_relation_is_scale_free(self) -> None:
        self.assertTrue(MODULE.non_climactic(4.0, 5.0))
        self.assertTrue(MODULE.non_climactic(5.0, 5.0))
        self.assertFalse(MODULE.non_climactic(5.1, 5.0))
        self.assertFalse(MODULE.non_climactic(-1.0, 5.0))

    @staticmethod
    def frame() -> pd.DataFrame:
        index = pd.date_range("2024-01-01", periods=12, freq="1min", tz="UTC")
        rows = []
        for _ in index:
            rows.append(
                {
                    "open": 100.0,
                    "high": 100.2,
                    "low": 99.8,
                    "close": 100.0,
                    "atr": 1.0,
                    "flow_60s": 0.0,
                    "ret_60s_bps": 0.0,
                    "basis_change_5m": 0.0,
                    "oi_change_xday_15m": 0.0,
                },
            )
        frame = pd.DataFrame(rows, index=index)
        # Break high pool at index 2.
        frame.loc[index[2], ["high", "close", "flow_60s", "ret_60s_bps", "basis_change_5m"]] = [101.4, 101.2, 0.5, 5.0, 1.5]
        # Hold/accept outside at index 3.
        frame.loc[index[3], ["high", "low", "close", "flow_60s", "ret_60s_bps", "basis_change_5m"]] = [101.5, 101.1, 101.3, 0.3, 3.0, 1.0]
        # Stay outside before retest.
        frame.loc[index[4], ["high", "low", "close"]] = [101.4, 101.1, 101.2]
        # First meaningful retest at index 5 with counter-flow and OI contraction.
        frame.loc[index[5], ["high", "low", "close", "flow_60s", "ret_60s_bps", "basis_change_5m", "oi_change_xday_15m"]] = [101.1, 100.7, 100.8, -0.4, -4.0, -1.0, -0.01]
        # Reclaim at index 6, non-climactic versus retest.
        frame.loc[index[6], ["high", "low", "close", "flow_60s", "ret_60s_bps", "basis_change_5m"]] = [101.3, 100.9, 101.2, 0.3, 3.0, 0.8]
        return frame

    @staticmethod
    def take() -> object:
        return MODULE.v24.PoolTake(
            shock_index=2,
            pool_id=1,
            pool_side=1,
            trade_side=-1,
            level=101.0,
            extreme=101.4,
            penetration_atr=0.4,
            age_bars=30,
            prominence_atr=0.5,
            touches=2,
        )

    def test_accepted_break_first_retest_reclaim_emits_long(self) -> None:
        frame = self.frame()
        with patch.object(MODULE.v24, "detect_external_pool_takes", return_value={2: [self.take()]}):
            intents, counts = MODULE.detect_external_break_retest_intents(
                frame,
                frame.index[0],
                frame.index[-1],
                SimpleNamespace(sweep_min_atr=0.03),
                SimpleNamespace(stop_buffer_atr=0.08),
            )
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, 1)
        self.assertEqual(intents[0].scenario, MODULE.SCENARIO)
        self.assertEqual(intents[0].event_indices, (2, 3, 5, 6))
        self.assertEqual(counts["confirmed_resumption"], 1)

    def test_close_inside_before_meaningful_retest_invalidates(self) -> None:
        frame = self.frame()
        frame.loc[frame.index[4], ["low", "close"]] = [100.99, 100.99]
        with patch.object(MODULE.v24, "detect_external_pool_takes", return_value={2: [self.take()]}):
            intents, _ = MODULE.detect_external_break_retest_intents(
                frame,
                frame.index[0],
                frame.index[-1],
                SimpleNamespace(sweep_min_atr=0.03),
                SimpleNamespace(stop_buffer_atr=0.08),
            )
        self.assertEqual(intents, [])

    def test_retest_without_oi_contraction_is_rejected(self) -> None:
        frame = self.frame()
        frame.loc[frame.index[5], "oi_change_xday_15m"] = 0.01
        with patch.object(MODULE.v24, "detect_external_pool_takes", return_value={2: [self.take()]}):
            intents, counts = MODULE.detect_external_break_retest_intents(
                frame,
                frame.index[0],
                frame.index[-1],
                SimpleNamespace(sweep_min_atr=0.03),
                SimpleNamespace(stop_buffer_atr=0.08),
            )
        self.assertEqual(intents, [])
        self.assertEqual(counts["retest_without_oi_contraction"], 1)


if __name__ == "__main__":
    unittest.main()
