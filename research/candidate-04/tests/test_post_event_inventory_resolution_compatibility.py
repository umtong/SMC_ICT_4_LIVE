from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import post_event_inventory_resolution_compiler_v2 as compatible


class CompatibleEffortTests(unittest.TestCase):
    def test_exact_horizon_notional_is_preserved(self) -> None:
        row = pd.Series(
            {
                "flow_300s": 0.4,
                "notional_300s": 5_000.0,
                "notional_60s": 700.0,
            }
        )
        self.assertEqual(
            compatible.directional_effort_compatible(row, 1, 300),
            2_000.0,
        )

    def test_missing_horizon_uses_completed_one_minute_notional(self) -> None:
        row = pd.Series(
            {
                "flow_300s": -0.5,
                "notional_60s": 1_000.0,
            }
        )
        self.assertEqual(
            compatible.directional_effort_compatible(row, -1, 300),
            2_500.0,
        )


class CompatibleStopTests(unittest.TestCase):
    def test_existing_sweep_buffer_name_is_resolved_without_numeric_change(self) -> None:
        frame = pd.DataFrame(
            {
                "low": [99.0, 98.0],
                "high": [101.0, 102.0],
                "atr": [2.0, 2.0],
            }
        )
        stop = compatible.response_stop_compatible(
            frame,
            0,
            1,
            1,
            SimpleNamespace(sweep_stop_buffer_atr=0.10),
        )
        self.assertEqual(stop, 97.8)


if __name__ == "__main__":
    unittest.main()
