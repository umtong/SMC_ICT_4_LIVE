from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import screen as module
from screen import Cascade
from screen import group_cascades


class LiquidationScreenTests(unittest.TestCase):
    def test_group_cascades_respects_side_and_time(self) -> None:
        frame = pd.DataFrame(
            {
                "ts": pd.to_datetime(
                    [
                        "2023-08-01T00:00:00Z",
                        "2023-08-01T00:00:05Z",
                        "2023-08-01T00:00:06Z",
                        "2023-08-01T00:00:30Z",
                    ],
                    utc=True,
                ),
                "direction": [-1, -1, 1, 1],
                "notional": [10.0, 20.0, 30.0, 40.0],
            },
        )
        cascades = group_cascades(frame)
        self.assertEqual(len(cascades), 3)
        self.assertEqual(cascades[0].notional, 30.0)
        self.assertEqual(cascades[1].direction, 1)

    def test_strictly_later_initiative_can_reach_target(self) -> None:
        old_min = module.MIN_HISTORY_CASCADES
        old_quantile = module.TAIL_QUANTILE
        try:
            module.MIN_HISTORY_CASCADES = 3
            module.TAIL_QUANTILE = 0.90
            index = pd.date_range("2023-08-01", periods=1_000, freq="s", tz="UTC")
            trades = pd.DataFrame(
                {
                    "close": np.full(1_000, 100.0),
                    "high": np.full(1_000, 100.0),
                    "low": np.full(1_000, 100.0),
                    "notional": np.full(1_000, 1_000_000.0),
                    "signed": np.zeros(1_000),
                    "flow": np.zeros(1_000),
                    "ret_bps": np.zeros(1_000),
                    "trades": np.ones(1_000),
                },
                index=index,
            )
            cascades = [
                Cascade(index[10], index[10], -1, 10_000.0, 1),
                Cascade(index[20], index[20], 1, 20_000.0, 1),
                Cascade(index[30], index[30], -1, 30_000.0, 1),
                Cascade(index[100], index[100], -1, 10_000_000.0, 4),
            ]
            # Parent second: forced selling and genuine downward delivery.
            trades.loc[index[100], ["close", "high", "low", "signed", "flow", "ret_bps"]] = [
                99.0,
                100.0,
                99.0,
                -800_000.0,
                -0.8,
                -100.5,
            ]
            # Strictly later opposite initiative.
            trades.loc[index[101], ["close", "high", "low", "signed", "flow", "ret_bps"]] = [
                99.2,
                99.2,
                99.0,
                800_000.0,
                0.8,
                20.18,
            ]
            # Reclaim the pre-cascade objective later.
            trades.loc[index[110], ["close", "high", "low"]] = [100.0, 100.0, 99.8]

            episodes, summary = module.screen(trades, cascades)
            executable = episodes[episodes["classification"].eq("EXECUTABLE")]
            self.assertEqual(len(executable), 1)
            self.assertEqual(executable.iloc[0]["outcome"], "TARGET_FIRST")
            self.assertGreater(executable.iloc[0]["planned_net_r"], 1.15)
            self.assertEqual(summary["target_first"], 1)
        finally:
            module.MIN_HISTORY_CASCADES = old_min
            module.TAIL_QUANTILE = old_quantile


if __name__ == "__main__":
    unittest.main()
