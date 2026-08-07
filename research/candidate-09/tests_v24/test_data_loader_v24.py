from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import data_loader
from state_engine import FlowBar, MINUTE_NS


class V24DataAlignmentContractTest(unittest.TestCase):
    def test_futures_index_and_metric_are_available_only_after_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            futures_path = root / "futures.zip"
            index_path = root / "index.zip"
            metrics_path = root / "metrics.zip"
            # Use a realistic millisecond epoch; the strategy observes the bar only
            # after the one-minute interval has completed.
            open_ms = 1_640_000_000_000
            completed_ns = open_ms * 1_000_000 + MINUTE_NS
            row = f"{open_ms},100,101,99,100,10,{open_ms + 59999},1000,10,5,500,0\n"
            with zipfile.ZipFile(futures_path, "w") as archive:
                archive.writestr("BTCUSDT-1m.csv", row)
            with zipfile.ZipFile(index_path, "w") as archive:
                archive.writestr(
                    "BTCUSDT-1m.csv",
                    f"{open_ms},100,100.5,99.5,100,0,{open_ms + 59999},0,0,0,0,0\n",
                )
            csv_text = (
                "create_time,symbol,sum_open_interest,sum_open_interest_value,"
                "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
                "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
                f"{open_ms // 1000},BTCUSDT,100000,10000000,1.1,1.2,1.0,\n"
            )
            with zipfile.ZipFile(metrics_path, "w") as archive:
                archive.writestr("BTCUSDT-metrics.csv", csv_text)

            futures = data_loader.parse_kline_archive(futures_path)
            index = data_loader.parse_index_archive(index_path)
            metrics = data_loader.parse_metric_archive(metrics_path, expected_symbol="BTCUSDT")
            self.assertEqual(futures[0].ts_ns, completed_ns)
            self.assertEqual(index[0].ts_ns, completed_ns)
            self.assertEqual(metrics[0].create_ns, open_ms * 1_000_000)
            self.assertEqual(metrics[0].available_ns, completed_ns)
            self.assertIsNone(metrics[0].taker_ratio)

            enriched = data_loader.enrich_bars(futures, metrics, index)[0]
            self.assertEqual(enriched.index_close, 100.0)
            self.assertEqual(enriched.metric_observed_ns, completed_ns)
            self.assertEqual(enriched.open_interest, 100000.0)
            self.assertIsNone(enriched.metric_taker_ratio)

    def test_metric_and_index_never_leak_forward(self):
        metrics = [
            data_loader.MetricSnapshot(
                create_ns=5 * MINUTE_NS,
                available_ns=6 * MINUTE_NS,
                symbol="BTCUSDT",
                open_interest=100.0,
                open_interest_value=10000.0,
                top_trader_account_ratio=1.0,
                top_trader_position_ratio=1.0,
                global_account_ratio=1.0,
                taker_ratio=None,
            ),
            data_loader.MetricSnapshot(
                create_ns=10 * MINUTE_NS,
                available_ns=11 * MINUTE_NS,
                symbol="BTCUSDT",
                open_interest=99.0,
                open_interest_value=9900.0,
                top_trader_account_ratio=1.0,
                top_trader_position_ratio=1.0,
                global_account_ratio=1.0,
                taker_ratio=None,
            ),
        ]
        futures = [
            FlowBar(
                ts_ns=i * MINUTE_NS,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=10.0,
                taker_buy_volume=5.0,
                trade_count=10,
            )
            for i in range(5, 12)
        ]
        indices = [
            data_loader.IndexBar(i * MINUTE_NS, 100.0, 101.0, 99.0, 100.0)
            for i in range(6, 12)
        ]
        enriched = data_loader.enrich_bars(futures, metrics, indices)
        self.assertIsNone(enriched[0].open_interest)
        self.assertIsNone(enriched[0].index_close)
        self.assertTrue(all(item.open_interest == 100.0 for item in enriched[1:-1]))
        self.assertEqual(enriched[-1].open_interest, 99.0)
        self.assertTrue(all(item.index_close == 100.0 for item in enriched[1:]))


if __name__ == "__main__":
    unittest.main()
