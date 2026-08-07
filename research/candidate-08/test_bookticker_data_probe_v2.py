"""Data-contract tests for the header-stable bookTicker streaming probe."""

from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

import pandas as pd

import bookticker_data_probe as base
from bookticker_data_probe_v2 import PROBE_REVISION, _read_chunks


class BookTickerDataContractTests(unittest.TestCase):
    def test_header_aliases_normalize_without_guessing_missing_fields(self) -> None:
        self.assertEqual(
            base._normalise_header(["u", "b", "B", "a", "A", "T", "E"]),
            base.BOOK_TICKER_COLUMNS,
        )
        self.assertIsNone(
            base._normalise_header(["u", "b", "B", "a", "A", "T", "unknown"])
        )

    def test_noncanonical_header_order_persists_across_multiple_chunks(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "BTCUSDT-bookTicker-2024-04-08.csv"
            header = ["E", "u", "a", "A", "b", "B", "T"]
            rows = [
                [1001, 11, 101.0, 3.0, 100.5, 2.0, 1000],
                [1011, 12, 101.1, 4.0, 100.6, 2.5, 1010],
                [1021, 13, 101.2, 5.0, 100.7, 3.0, 1020],
                [1031, 14, 101.3, 6.0, 100.8, 3.5, 1030],
            ]
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)
            archive_path = root / "BTCUSDT-bookTicker-2024-04-08.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(csv_path, arcname=csv_path.name)

            member, iterator = _read_chunks(archive_path, chunksize=2)
            materialized = pd.concat(list(iterator), ignore_index=True)

        self.assertEqual(member, csv_path.name)
        self.assertEqual(tuple(materialized.columns), base.BOOK_TICKER_COLUMNS)
        self.assertEqual(len(materialized.index), 4)
        self.assertEqual(materialized["update_id"].astype(int).tolist(), [11, 12, 13, 14])
        self.assertEqual(
            materialized["best_bid_price"].astype(float).tolist(),
            [100.5, 100.6, 100.7, 100.8],
        )
        self.assertEqual(
            materialized["best_ask_price"].astype(float).tolist(),
            [101.0, 101.1, 101.2, 101.3],
        )
        self.assertEqual(
            materialized["transaction_time"].astype(int).tolist(),
            [1000, 1010, 1020, 1030],
        )
        self.assertEqual(
            materialized["event_time"].astype(int).tolist(),
            [1001, 1011, 1021, 1031],
        )

    def test_headerless_archive_uses_documented_canonical_order(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "data.csv"
            rows = [
                [1, 100.0, 2.0, 100.1, 3.0, 1000, 1001],
                [2, 100.1, 2.5, 100.2, 3.5, 1010, 1011],
                [3, 100.2, 3.0, 100.3, 4.0, 1020, 1021],
            ]
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(rows)
            archive_path = root / "data.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(csv_path, arcname=csv_path.name)

            _, iterator = _read_chunks(archive_path, chunksize=1)
            materialized = pd.concat(list(iterator), ignore_index=True)

        self.assertEqual(tuple(materialized.columns), base.BOOK_TICKER_COLUMNS)
        self.assertEqual(materialized["update_id"].astype(int).tolist(), [1, 2, 3])

    def test_weighted_quantiles_use_conservative_higher_observation(self) -> None:
        counts = Counter({0.1: 50, 0.2: 40, 0.5: 10})
        # This is an execution-tail data contract.  At an exact empirical boundary the next
        # observed spread is deliberately selected rather than interpolating or understating risk.
        self.assertEqual(base._weighted_quantile(counts, 0.00), 0.1)
        self.assertEqual(base._weighted_quantile(counts, 0.50), 0.2)
        self.assertEqual(base._weighted_quantile(counts, 0.90), 0.5)
        self.assertEqual(base._weighted_quantile(counts, 0.99), 0.5)
        self.assertEqual(base._weighted_quantile(counts, 1.00), 0.5)

    def test_source_path_and_revision_are_predeclared(self) -> None:
        filename, url, checksum = base._source_urls(
            "BTCUSDT",
            pd.Timestamp("2024-04-08").date(),
        )
        self.assertEqual(filename, "BTCUSDT-bookTicker-2024-04-08.zip")
        self.assertIn("/data/futures/um/daily/bookTicker/BTCUSDT/", url)
        self.assertEqual(checksum, f"{url}.CHECKSUM")
        self.assertEqual(
            PROBE_REVISION,
            "BINANCE_USDM_BOOKTICKER_DATA_CONTRACT_V2_HEADER_STABLE_STREAMING",
        )

    def test_probe_source_contains_no_strategy_or_backtest(self) -> None:
        source = Path(__file__).with_name("bookticker_data_probe_v2.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "BacktestEngine(",
            "submit_order",
            "risk_sized_quantity",
            "realized_pnl",
            "future_high",
            "future_low",
            "win_rate",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
