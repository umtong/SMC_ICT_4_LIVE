from __future__ import annotations

import gzip
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from v9_tardis_liquidation_study import TardisArchive
from v9_tardis_liquidation_study import _global_cluster
from v9_tardis_liquidation_study import apply_causal_event_thresholds
from v9_tardis_liquidation_study import read_tardis_derivative
from v9_tardis_liquidation_study import read_tardis_liquidations


class Candidate16V9TardisStudyTests(unittest.TestCase):
    def _gzip_csv(self, text: str) -> Path:
        root = Path(tempfile.mkdtemp())
        path = root / "sample.csv.gz"
        path.write_bytes(gzip.compress(text.encode("utf-8")))
        return path

    def test_first_day_free_sample_url_contract(self) -> None:
        archive = TardisArchive(
            "liquidations",
            date(2023, 10, 1),
            "BTCUSDT",
        )
        self.assertEqual(
            archive.url,
            "https://datasets.tardis.dev/v1/binance-futures/"
            "liquidations/2023/10/01/BTCUSDT.csv.gz",
        )

    def test_normalized_liquidation_side_contract(self) -> None:
        path = self._gzip_csv(
            "exchange,symbol,timestamp,local_timestamp,id,side,price,amount\n"
            "binance-futures,BTCUSDT,1632009737493000,1632009737505152,,sell,100,2\n"
            "binance-futures,BTCUSDT,1632009738493000,1632009738505152,,buy,101,3\n",
        )
        frame = read_tardis_liquidations(path)
        self.assertEqual(len(frame), 1)
        self.assertAlmostEqual(float(frame.iloc[0]["long_liq_notional"]), 200.0)
        self.assertAlmostEqual(float(frame.iloc[0]["short_liq_notional"]), 303.0)

    def test_derivative_ticker_uses_last_completed_minute_state(self) -> None:
        path = self._gzip_csv(
            "exchange,symbol,timestamp,local_timestamp,funding_timestamp,"
            "funding_rate,predicted_funding_rate,open_interest,last_price,"
            "index_price,mark_price\n"
            "binance-futures,BTCUSDT,1632009737000000,1632009737001000,,"
            "0.0001,,10,100,99,99.5\n"
            "binance-futures,BTCUSDT,1632009759000000,1632009759001000,,"
            "0.0001,,11,101,100,100.5\n",
        )
        frame = read_tardis_derivative(path)
        self.assertEqual(len(frame), 1)
        self.assertEqual(float(frame.iloc[0]["open_interest"]), 11.0)
        self.assertEqual(float(frame.iloc[0]["mark_price_tick"]), 100.5)

    def test_event_quantile_is_shifted_before_current_observation(self) -> None:
        rows = 110
        panel = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"] * rows,
                "minute": pd.date_range("2023-01-01", periods=rows, freq="min", tz="UTC"),
                "dominant_liq": [1.0] * 100 + [100.0] + [1.0] * 9,
                "liq_dominance": [1.0] * rows,
                "directional_perp_return": [0.001] * rows,
                "directional_vwap_deviation": [0.001] * rows,
                "open_interest": [1.0] * rows,
            },
        )
        result = apply_causal_event_thresholds(panel)
        current = result.iloc[100]
        self.assertAlmostEqual(float(current["liq_threshold_99"]), 1.0)
        self.assertTrue(bool(current["event_candidate"]))

    def test_simultaneous_cross_asset_events_become_one_episode(self) -> None:
        moments = pd.to_datetime(
            ["2023-01-01T12:00:00Z", "2023-01-01T12:02:00Z", "2023-01-01T12:08:00Z"],
            utc=True,
        )
        events = pd.DataFrame(
            {
                "minute": moments,
                "symbol": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                "event_direction": [-1, -1, -1],
                "dominant_liq": [100.0, 200.0, 50.0],
                "liq_share_of_perp_volume": [0.10, 0.20, 0.05],
            },
        )
        clustered = _global_cluster(events)
        self.assertEqual(len(clustered), 2)
        self.assertEqual(str(clustered.iloc[0]["symbol"]), "ETHUSDT")
        self.assertEqual(int(clustered.iloc[0]["cluster_symbol_count"]), 2)
        self.assertEqual(str(clustered.iloc[0]["cluster_symbols"]), "BTCUSDT,ETHUSDT")


if __name__ == "__main__":
    unittest.main()
