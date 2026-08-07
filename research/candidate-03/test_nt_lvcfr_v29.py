from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from derive_nt_lvcfr_v29_signals import (
    aligned_leaders,
    find_retest,
    read_archive,
)


class ParserTests(unittest.TestCase):
    def test_header_and_microsecond_timestamp_are_supported(self) -> None:
        directory = Path(tempfile.mkdtemp())
        path = directory / "sample.zip"
        header = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "count", "taker_buy_volume",
            "taker_buy_quote_volume", "ignore",
        ]
        row = [
            1_704_067_200_000_000, 100.0, 101.0, 99.0, 100.5, 1.0,
            1_704_067_259_999_000, 100.0, 10, 0.6, 60.0, 0,
        ]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "sample.csv",
                ",".join(header) + "\n" + ",".join(map(str, row)) + "\n",
            )
        frame = read_archive(path, "btcusdt_futures")
        self.assertEqual(len(frame), 1)
        self.assertEqual(int(frame.iloc[0].open_time_ms), 1_704_067_200_000)


class LeaderAgreementTests(unittest.TestCase):
    def row(self) -> object:
        values = {}
        for symbol in ("ethusdt", "solusdt", "xrpusdt"):
            values[f"{symbol}_futures_return"] = 0.01
            values[f"{symbol}_spot_return"] = 0.009
            values[f"{symbol}_futures_flow"] = 0.25
            values[f"{symbol}_spot_flow"] = 0.15
        return type("Row", (), values)()

    def test_at_least_two_leaders_must_agree(self) -> None:
        row = self.row()
        self.assertEqual(len(aligned_leaders(row, 1)), 3)
        row.xrpusdt_spot_flow = -0.10
        self.assertEqual(len(aligned_leaders(row, 1)), 2)
        row.solusdt_futures_return = -0.01
        self.assertEqual(len(aligned_leaders(row, 1)), 1)


class RetestTests(unittest.TestCase):
    def frame(self, btc_flow: float, leader_count: int) -> pd.DataFrame:
        row = {
            "open_time_ms": 300_000,
            "btcusdt_futures_open": 100.0,
            "btcusdt_futures_high": 101.0,
            "btcusdt_futures_low": 99.8,
            "btcusdt_futures_close": 100.8,
            "btcusdt_futures_quote": 1_000_000.0,
            "btcusdt_futures_buy_quote": 1_000_000.0 * (btc_flow + 1.0) / 2.0,
            "btcusdt_spot_open": 100.0,
            "btcusdt_spot_high": 101.0,
            "btcusdt_spot_low": 99.8,
            "btcusdt_spot_close": 100.7,
            "btcusdt_spot_quote": 1_000_000.0,
            "btcusdt_spot_buy_quote": 1_000_000.0 * (btc_flow + 1.0) / 2.0,
        }
        for index, symbol in enumerate(("ethusdt", "solusdt", "xrpusdt")):
            aligned = index < leader_count
            sign = 1.0 if aligned else -1.0
            for market in ("futures", "spot"):
                row[f"{symbol}_{market}_open"] = 100.0
                row[f"{symbol}_{market}_close"] = 100.5 if aligned else 99.5
                row[f"{symbol}_{market}_high"] = 100.7
                row[f"{symbol}_{market}_low"] = 99.3
                row[f"{symbol}_{market}_quote"] = 1_000_000.0
                row[f"{symbol}_{market}_buy_quote"] = 1_000_000.0 * (0.2 * sign + 1.0) / 2.0
        return pd.DataFrame([row])

    def test_completed_pullback_defense_requires_btc_and_two_leaders(self) -> None:
        accepted = find_retest(
            self.frame(0.25, 2), start_ms=300_000, direction=1, zone_mid=100.0
        )
        self.assertIsNotNone(accepted)
        self.assertIsNone(
            find_retest(
                self.frame(-0.10, 2), start_ms=300_000, direction=1, zone_mid=100.0
            )
        )
        self.assertIsNone(
            find_retest(
                self.frame(0.25, 1), start_ms=300_000, direction=1, zone_mid=100.0
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
