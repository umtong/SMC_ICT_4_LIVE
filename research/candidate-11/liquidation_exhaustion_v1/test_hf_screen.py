from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from hf_screen import Cascade
from hf_screen import group_cascades
from hf_screen import screen


class EventLevelLiquidationTests(unittest.TestCase):
    def test_cascade_grouping_is_side_and_time_causal(self) -> None:
        frame = pd.DataFrame(
            {
                "ts": pd.to_datetime(
                    [
                        "2026-04-29T15:00:00Z",
                        "2026-04-29T15:00:02Z",
                        "2026-04-29T15:00:03Z",
                        "2026-04-29T15:00:10Z",
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

    def test_later_trade_and_book_reversal_reaches_target(self) -> None:
        index = pd.date_range(
            "2026-04-29T15:00:00Z",
            periods=500,
            freq="s",
            tz="UTC",
        )
        trades = pd.DataFrame(
            {
                "close": np.full(500, 100.0),
                "high": np.full(500, 100.0),
                "low": np.full(500, 100.0),
                "notional": np.full(500, 1_000_000.0),
                "signed": np.zeros(500),
                "trades": np.ones(500),
                "flow": np.zeros(500),
                "ret_bps": np.zeros(500),
            },
            index=index,
        )
        book = pd.DataFrame(
            {
                "bid_price": np.full(500, 99.99),
                "bid_qty": np.full(500, 5.0),
                "ask_price": np.full(500, 100.01),
                "ask_qty": np.full(500, 5.0),
                "mid": np.full(500, 100.0),
                "spread_bps": np.full(500, 2.0),
                "imbalance": np.zeros(500),
                "microprice_premium_bps": np.zeros(500),
            },
            index=index,
        )
        cascades = [
            Cascade(index[i], index[i], -1 if i % 2 else 1, 1_000.0 + i, 1)
            for i in range(1, 21)
        ]
        cascades.append(Cascade(index[100], index[100], -1, 1_000_000.0, 3))
        trades.loc[index[100], ["close", "high", "low", "signed", "flow", "ret_bps"]] = [
            99.0,
            100.0,
            99.0,
            -800_000.0,
            -0.8,
            -100.5,
        ]
        trades.loc[index[101], ["close", "high", "low", "signed", "flow", "ret_bps"]] = [
            99.2,
            99.2,
            99.0,
            800_000.0,
            0.8,
            20.18,
        ]
        book.loc[index[101], [
            "bid_price",
            "bid_qty",
            "ask_price",
            "ask_qty",
            "mid",
            "spread_bps",
            "imbalance",
            "microprice_premium_bps",
        ]] = [99.19, 10.0, 99.21, 1.0, 99.20, 2.0, 9.0 / 11.0, 0.08]
        trades.loc[index[110], ["close", "high", "low"]] = [100.0, 100.0, 99.8]

        episodes, summary = screen(trades, book, cascades)
        executable = episodes[episodes["classification"].eq("EXECUTABLE")]
        self.assertEqual(len(executable), 1)
        self.assertEqual(executable.iloc[0]["outcome"], "TARGET_FIRST")
        self.assertGreater(executable.iloc[0]["planned_net_r"], 1.15)
        self.assertEqual(summary["target_first"], 1)


if __name__ == "__main__":
    unittest.main()
