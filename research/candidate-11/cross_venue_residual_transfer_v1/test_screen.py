from __future__ import annotations

from datetime import datetime, timezone
import unittest

import numpy as np
import pandas as pd

from screen import _bybit_timestamp
from screen import screen


def synthetic_state(*, close_target: bool, remaining_gap_bps: float = 55.0) -> pd.DataFrame:
    n = 2_100
    index = pd.date_range("2023-08-01", periods=n, freq="s", tz="UTC")
    frame = pd.DataFrame(
        {
            "binance_close": np.full(n, 100.0),
            "bybit_close": np.full(n, 100.0),
            "binance_ret_bps": np.zeros(n),
            "bybit_ret_bps": np.zeros(n),
            "binance_flow": np.zeros(n),
            "bybit_flow": np.zeros(n),
            "basis_baseline_bps": np.zeros(n),
            "gap_bps": np.zeros(n),
            "gap_tail_bps": np.ones(n),
        },
        index=index,
    )
    i = 1_900
    event_gap = 60.0
    frame.iloc[i, frame.columns.get_loc("bybit_close")] = 100.0 * np.exp(event_gap / 10_000.0)
    frame.iloc[i, frame.columns.get_loc("bybit_ret_bps")] = event_gap
    frame.iloc[i, frame.columns.get_loc("bybit_flow")] = 0.8
    frame.iloc[i, frame.columns.get_loc("gap_bps")] = event_gap
    frame.iloc[i, frame.columns.get_loc("gap_tail_bps")] = 30.0

    j = i + 1
    entry = 100.05
    fair = entry * np.exp(remaining_gap_bps / 10_000.0)
    frame.iloc[j, frame.columns.get_loc("binance_close")] = entry
    frame.iloc[j, frame.columns.get_loc("bybit_close")] = fair
    frame.iloc[j, frame.columns.get_loc("binance_ret_bps")] = 5.0
    frame.iloc[j, frame.columns.get_loc("binance_flow")] = 0.7
    frame.iloc[j, frame.columns.get_loc("bybit_ret_bps")] = 0.0
    frame.iloc[j, frame.columns.get_loc("gap_bps")] = remaining_gap_bps
    frame.iloc[j, frame.columns.get_loc("gap_tail_bps")] = 30.0

    for k in range(j + 1, min(n, j + 121)):
        frame.iloc[k, frame.columns.get_loc("gap_bps")] = remaining_gap_bps
        frame.iloc[k, frame.columns.get_loc("bybit_close")] = fair
        frame.iloc[k, frame.columns.get_loc("binance_close")] = entry
    if close_target:
        frame.iloc[j + 10, frame.columns.get_loc("binance_close")] = fair
    return frame


class CrossVenueScreenTests(unittest.TestCase):
    def test_bybit_fractional_unix_seconds(self) -> None:
        parsed = _bybit_timestamp(pd.Series([1690848000.123456]))
        self.assertEqual(parsed.iloc[0].tzinfo, timezone.utc)
        self.assertEqual(parsed.iloc[0].year, 2023)

    def test_target_before_stop_episode(self) -> None:
        episodes, summary = screen(synthetic_state(close_target=True))
        executable = episodes[episodes["classification"].eq("EXECUTABLE")]
        self.assertEqual(len(executable), 1)
        self.assertEqual(executable.iloc[0]["outcome"], "TARGET_FIRST")
        self.assertGreater(executable.iloc[0]["planned_net_r"], 1.15)
        self.assertEqual(summary["target_first"], 1)

    def test_cost_geometry_rejects_small_gap(self) -> None:
        episodes, summary = screen(
            synthetic_state(close_target=False, remaining_gap_bps=25.0),
        )
        self.assertEqual(summary["executable_episodes"], 0)
        self.assertTrue(
            episodes["classification"].isin(
                {"INSUFFICIENT_NET_R_AFTER_COSTS", "INVALID_EXECUTABLE_GEOMETRY"},
            ).any(),
        )


if __name__ == "__main__":
    unittest.main()
