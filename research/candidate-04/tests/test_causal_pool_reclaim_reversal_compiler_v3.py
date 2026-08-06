from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "causal_pool_reclaim_reversal_compiler_v3.py"
SPEC = importlib.util.spec_from_file_location("candidate04_pool_reclaim_v3_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CausalPoolReclaimTests(unittest.TestCase):
    def test_reversal_requires_flow_return_basis_and_relative_size(self) -> None:
        state = MODULE.base.base.TurnState(0.3, 3.0, 1.0)
        self.assertTrue(MODULE.base.base.reversal_confirmed(state, 5.0))
        self.assertFalse(MODULE.base.base.reversal_confirmed(state, 2.0))
        self.assertFalse(
            MODULE.base.base.reversal_confirmed(
                MODULE.base.base.TurnState(0.3, 3.0, -1.0),
                5.0,
            ),
        )

    @staticmethod
    def frame() -> pd.DataFrame:
        index = pd.date_range("2024-01-01", periods=8, freq="1min", tz="UTC")
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
        # Attack through a causal high pool.
        frame.loc[index[2], ["high", "close", "flow_60s", "ret_60s_bps", "basis_change_5m"]] = [101.3, 101.1, 0.5, 5.0, 1.0]
        # Later exact reclaim with short-side flow/return/basis.
        frame.loc[index[3], ["high", "low", "close", "flow_60s", "ret_60s_bps", "basis_change_5m"]] = [101.2, 100.6, 100.8, -0.4, -3.0, -0.8]
        return frame

    @staticmethod
    def take() -> object:
        return MODULE.base.base.v24.PoolTake(
            shock_index=2,
            pool_id=1,
            pool_side=1,
            trade_side=-1,
            level=101.0,
            extreme=101.3,
            penetration_atr=0.3,
            age_bars=30,
            prominence_atr=0.5,
            touches=2,
        )

    def test_attack_then_exact_reclaim_emits_short(self) -> None:
        frame = self.frame()
        with patch.object(
            MODULE.base.base.v24,
            "detect_external_pool_takes",
            return_value={2: [self.take()]},
        ):
            intents, counts = MODULE.base.detect_causal_pool_reclaim_intents(
                frame,
                frame.index[0],
                frame.index[-1],
                SimpleNamespace(),
                SimpleNamespace(stop_buffer_atr=0.08),
            )
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, -1)
        self.assertEqual(intents[0].event_indices, (2, 3))
        self.assertEqual(counts["confirmed_reversal"], 1)

    def test_reclaim_without_basis_turn_is_rejected(self) -> None:
        frame = self.frame()
        frame.loc[frame.index[3], "basis_change_5m"] = 0.8
        with patch.object(
            MODULE.base.base.v24,
            "detect_external_pool_takes",
            return_value={2: [self.take()]},
        ):
            intents, _ = MODULE.base.detect_causal_pool_reclaim_intents(
                frame,
                frame.index[0],
                frame.index[-1],
                SimpleNamespace(),
                SimpleNamespace(stop_buffer_atr=0.08),
            )
        self.assertEqual(intents, [])


if __name__ == "__main__":
    unittest.main()
