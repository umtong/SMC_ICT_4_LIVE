from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "session_inventory_acceptance_no_oi_compiler.py"
SPEC = importlib.util.spec_from_file_location("candidate04_session_no_oi_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SessionAcceptanceNoOiTests(unittest.TestCase):
    def test_negative_oi_does_not_block_otherwise_complete_state(self) -> None:
        row = pd.Series(
            {
                "flow_60s": 0.4,
                "ret_60s_bps": 3.0,
                "metric_oi_change_15m": -0.02,
                "basis_change_5m": 1.5,
            },
        )
        passed, details = MODULE.alignment_without_oi(row, 1)
        self.assertTrue(passed)
        self.assertLess(details["open_interest_change_15m"], 0.0)
        self.assertEqual(details["open_interest_gate_required"], 0.0)

    def test_basis_and_executed_flow_remain_required(self) -> None:
        row = pd.Series(
            {
                "flow_60s": 0.4,
                "ret_60s_bps": 3.0,
                "metric_oi_change_15m": 0.02,
                "basis_change_5m": -0.5,
            },
        )
        passed, _ = MODULE.alignment_without_oi(row, 1)
        self.assertFalse(passed)


if __name__ == "__main__":
    unittest.main()
