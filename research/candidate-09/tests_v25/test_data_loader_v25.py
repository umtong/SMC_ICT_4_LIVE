from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import data_loader_v25 as loader
import state_engine_v25_direct as v25

MINUTE = v25.MINUTE_NS


class V25DataAlignmentContractTest(unittest.TestCase):
    def test_perpetual_spot_and_metric_are_available_only_after_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            futures_path = root / "futures.zip"
            spot_path = root / "spot.zip"
            metrics_path = root / "metrics.zip"
            open_ms = 1_640_000_000_000
            completed_ns = open_ms * 1_000_000 + MINUTE
            futures_row = f"{open_ms},100,101,99,100,10,{open_ms+59999},1000,10,5,500,0\n"
            spot_row = f"{open_ms},100,100.5,99.5,100,10,{open_ms+59999},1000,10,5,500,0\n"
            with zipfile.ZipFile(futures_path, "w") as archive:
                archive.writestr("BTCUSDT-1m.csv", futures_row)
            with zipfile.ZipFile(spot_path, "w") as archive:
                archive.writestr("BTCUSDT-1m.csv", spot_row)
            csv_text = (
                "create_time,symbol,sum_open_interest,sum_open_interest_value,"
                "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
                "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
                f"{open_ms // 1000},BTCUSDT,100000,10000000,1.1,1.2,1.0,\n"
            )
            with zipfile.ZipFile(metrics_path, "w") as archive:
                archive.writestr("BTCUSDT-metrics.csv", csv_text)

            futures = loader.parse_kline_archive(futures_path)
            spot = loader.parse_spot_archive(spot_path)
            metrics = loader.parse_metric_archive(metrics_path, expected_symbol="BTCUSDT")
            self.assertEqual(futures[0].ts_ns, completed_ns)
            self.assertEqual(spot[0].ts_ns, completed_ns)
            self.assertEqual(metrics[0].available_ns, completed_ns)
            enriched = loader.enrich_bars(futures, metrics, spot)[0]
            self.assertEqual(enriched.spot_close, 100.0)
            self.assertEqual(enriched.metric_observed_ns, completed_ns)
            self.assertEqual(enriched.open_interest, 100000.0)

    def test_metric_and_spot_never_leak_forward_or_fill_internal_gap(self):
        metrics = [loader.MetricSnapshot(
            create_ns=5*MINUTE, available_ns=6*MINUTE, symbol="BTCUSDT",
            open_interest=100.0, open_interest_value=10000.0,
            top_trader_account_ratio=1.0, top_trader_position_ratio=1.0,
            global_account_ratio=1.0, taker_ratio=None,
        )]
        futures = [v25.FlowBar(
            ts_ns=i*MINUTE, open=100.0, high=101.0, low=99.0, close=100.0,
            volume=10.0, taker_buy_volume=5.0, trade_count=10,
        ) for i in (5, 6, 7)]
        spot = [
            loader.SpotBar(6*MINUTE, 100.0, 101.0, 99.0, 100.0),
            loader.SpotBar(7*MINUTE, 101.0, 102.0, 100.0, 101.0),
        ]
        enriched = loader.enrich_bars(futures, metrics, spot)
        self.assertIsNone(enriched[0].open_interest)
        self.assertIsNone(enriched[0].spot_close)
        self.assertEqual(enriched[1].open_interest, 100.0)
        self.assertEqual(enriched[1].spot_close, 100.0)
        self.assertEqual(enriched[2].spot_close, 101.0)

        missing = loader.enrich_bars(futures, (), spot[:1])
        self.assertIsNone(missing[2].spot_close)

    def test_duplicate_spot_timestamp_is_rejected(self):
        future = [v25.FlowBar(
            ts_ns=6*MINUTE, open=100.0, high=101.0, low=99.0, close=100.0,
            volume=10.0, taker_buy_volume=5.0, trade_count=10,
        )]
        item = loader.SpotBar(6*MINUTE, 100.0, 101.0, 99.0, 100.0)
        with self.assertRaisesRegex(ValueError, "duplicate spot timestamp"):
            loader.enrich_bars(future, (), (item, item))

    def test_official_paths_keep_spot_and_futures_archives_separate(self):
        cache = loader.BinanceVisionCache(Path("/tmp/c09-v25"))
        self.assertIn("/data/futures/um", loader.FUTURES_BASE_URL)
        self.assertTrue(loader.SPOT_BASE_URL.endswith("/data/spot"))
        self.assertNotEqual(loader.FUTURES_BASE_URL, loader.SPOT_BASE_URL)
        self.assertEqual(cache.symbol, "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
