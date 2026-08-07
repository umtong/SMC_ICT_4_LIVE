from __future__ import annotations

import unittest

import pandas as pd

from data_aggtrades_seeded import complete_no_trade_seconds_with_seed


class SeededAggTradeClockTests(unittest.TestCase):
    @staticmethod
    def _row(timestamp_ns: int, price: float) -> dict:
        return {
            "timestamp_ns": timestamp_ns,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1.0,
            "quote_volume": price,
            "taker_buy_quote": price,
            "taker_sell_quote": 0.0,
            "trade_count": 1,
            "first_trade_ns": timestamp_ns - 500_000_000,
            "last_trade_ns": timestamp_ns - 500_000_000,
        }

    def test_leading_no_trade_seconds_use_only_pre_window_trade(self) -> None:
        base = 1_735_689_600_000_000_000
        records = pd.DataFrame(
            [
                self._row(base - 1, 99.0),
                self._row(base + 2_999_999_999, 102.0),
            ]
        )
        completed, diagnostics = complete_no_trade_seconds_with_seed(
            records,
            load_start_ns=base,
            trade_end_ns=base + 4_000_000_000,
        )
        self.assertEqual(len(completed.index), 4)
        for index in (0, 1):
            row = completed.iloc[index]
            self.assertFalse(bool(row["had_trade"]))
            self.assertEqual(float(row["close"]), 99.0)
            self.assertEqual(float(row["quote_volume"]), 0.0)
            self.assertEqual(int(row["trade_count"]), 0)
        self.assertTrue(bool(completed.iloc[2]["had_trade"]))
        self.assertEqual(float(completed.iloc[2]["close"]), 102.0)
        self.assertEqual(diagnostics["leading_seeded_zero_flow_seconds"], 2)
        self.assertEqual(diagnostics["causal_seed_timestamp_ns"], base - 1)

    def test_future_first_trade_cannot_seed_the_window(self) -> None:
        base = 1_735_689_600_000_000_000
        records = pd.DataFrame(
            [self._row(base + 2_999_999_999, 102.0)]
        )
        with self.assertRaises(RuntimeError):
            complete_no_trade_seconds_with_seed(
                records,
                load_start_ns=base,
                trade_end_ns=base + 4_000_000_000,
            )


if __name__ == "__main__":
    unittest.main()
