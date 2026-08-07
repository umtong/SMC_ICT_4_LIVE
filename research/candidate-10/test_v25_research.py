from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from c10_v25_research import (
    _QuoteBucket,
    _side_changes,
    aggregate_book_ticker_archives,
    align_liquidity_rows,
)


class _TradeBucket:
    def __init__(self, *, close: float, quote: float, buy: float, count: int = 1):
        self.close = close
        self.quote_volume = quote
        self.taker_buy_quote = buy
        self.trade_count = count


class V25ResearchDataTests(unittest.TestCase):
    def test_same_price_size_change_is_add_or_remove(self) -> None:
        self.assertEqual(
            _side_changes(
                previous_price=100.0,
                previous_size=2.0,
                price=100.0,
                size=3.0,
                is_bid=True,
            ),
            (1.0, 0.0),
        )
        self.assertEqual(
            _side_changes(
                previous_price=100.0,
                previous_size=3.0,
                price=100.0,
                size=1.0,
                is_bid=True,
            ),
            (0.0, 2.0),
        )

    def test_bookticker_boundary_event_belongs_to_next_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BTCUSDT-bookTicker-2023-10-16.zip"
            csv_name = "BTCUSDT-bookTicker-2023-10-16.csv"
            with zipfile.ZipFile(path, "w") as archive:
                rows = [
                    [
                        "update_id",
                        "best_bid_price",
                        "best_bid_qty",
                        "best_ask_price",
                        "best_ask_qty",
                        "transaction_time",
                        "event_time",
                    ],
                    ["1", "100.0", "2.0", "100.1", "2.0", "999", "999"],
                    ["2", "100.0", "3.0", "100.1", "1.0", "1000", "1000"],
                ]
                text = "\n".join(",".join(row) for row in rows) + "\n"
                archive.writestr(csv_name, text)
            buckets, quality = aggregate_book_ticker_archives(
                [path],
                bucket_seconds=1,
            )
            self.assertIn(1_000_000_000, buckets)
            self.assertIn(2_000_000_000, buckets)
            self.assertEqual(quality["nonmonotonic_event_time_count"], 0)
            self.assertGreater(buckets[2_000_000_000].ofi_qty, 0.0)

    def test_dense_alignment_carries_only_prior_quote_and_zeroes_flow(self) -> None:
        first = _QuoteBucket(
            open_mid=100.05,
            high_mid=100.05,
            low_mid=100.05,
            close_mid=100.05,
            bid_price=100.0,
            ask_price=100.1,
            bid_size=2.0,
            ask_size=2.0,
            spread_sum=0.1,
            max_spread=0.1,
            quote_updates=1,
            ofi_qty=5.0,
            bid_add_qty=1.0,
            bid_remove_qty=0.0,
            ask_add_qty=0.0,
            ask_remove_qty=1.0,
            first_event_ts_ns=500_000_000,
            last_event_ts_ns=500_000_000,
        )
        third = _QuoteBucket(
            open_mid=100.15,
            high_mid=100.15,
            low_mid=100.15,
            close_mid=100.15,
            bid_price=100.1,
            ask_price=100.2,
            bid_size=3.0,
            ask_size=1.0,
            spread_sum=0.1,
            max_spread=0.1,
            quote_updates=1,
            ofi_qty=2.0,
            bid_add_qty=1.0,
            bid_remove_qty=0.0,
            ask_add_qty=0.0,
            ask_remove_qty=1.0,
            first_event_ts_ns=2_500_000_000,
            last_event_ts_ns=2_500_000_000,
        )
        rows, quality = align_liquidity_rows(
            {1_000_000_000: first, 3_000_000_000: third},
            {
                1_000_000_000: _TradeBucket(
                    close=100.0,
                    quote=1000.0,
                    buy=750.0,
                ),
            },
            bucket_seconds=1,
        )
        self.assertEqual(len(rows), 3)
        middle = rows[1]
        self.assertEqual(middle["mid_close"], first.close_mid)
        self.assertEqual(middle["quote_updates"], 0)
        self.assertEqual(middle["ofi_qty"], 0.0)
        self.assertEqual(middle["trade_quote_volume"], 0.0)
        self.assertEqual(quality["missing_quote_interval_count"], 1)


if __name__ == "__main__":
    unittest.main()
