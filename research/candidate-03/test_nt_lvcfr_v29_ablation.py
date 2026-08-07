from __future__ import annotations

import unittest

import pandas as pd

from derive_nt_lvcfr_v29_signals import find_retest


class PostShockLeaderPersistenceAblationTests(unittest.TestCase):
    def frame(self, btc_flow: float) -> pd.DataFrame:
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
        # Leaders have stopped moving after their originating shock. This must
        # not invalidate a BTC pullback defense once BTC futures and spot agree.
        for symbol in ("ethusdt", "solusdt", "xrpusdt"):
            for market in ("futures", "spot"):
                row[f"{symbol}_{market}_open"] = 100.0
                row[f"{symbol}_{market}_close"] = 100.0
                row[f"{symbol}_{market}_high"] = 100.1
                row[f"{symbol}_{market}_low"] = 99.9
                row[f"{symbol}_{market}_quote"] = 1_000_000.0
                row[f"{symbol}_{market}_buy_quote"] = 500_000.0
        return pd.DataFrame([row])

    def test_btc_retest_defense_survives_flat_leaders(self) -> None:
        self.assertIsNotNone(
            find_retest(
                self.frame(0.25),
                start_ms=300_000,
                direction=1,
                zone_mid=100.0,
            )
        )

    def test_btc_flow_remains_mandatory(self) -> None:
        self.assertIsNone(
            find_retest(
                self.frame(-0.10),
                start_ms=300_000,
                direction=1,
                zone_mid=100.0,
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
