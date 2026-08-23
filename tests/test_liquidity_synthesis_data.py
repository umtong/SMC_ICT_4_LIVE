from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest
import zipfile

from nautilus_trader.model.instruments import CryptoPerpetual

from smc_ict_4.episode_policy_live.domain import SYMBOLS
from smc_ict_4.episode_policy_live.nautilus_data import (
    BinanceKline1mLoader,
    BinanceKlineDataError,
    iter_policy_flow_sidecar,
    make_binance_usdm_instruments,
    query_catalog_bars,
    write_parquet_catalog,
)


HEADER = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)


def row(open_ms: int, price: float = 100.0) -> list[object]:
    return [
        open_ms,
        price,
        price + 2,
        price - 1,
        price + 1,
        10,
        open_ms + 59_999,
        1005,
        20,
        6,
        603,
        0,
    ]


class NautilusDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def sources(self, clocks: dict[str, list[int]], *, header: bool = True) -> dict[str, Path]:
        result: dict[str, Path] = {}
        for offset, symbol in enumerate(SYMBOLS):
            path = self.root / f"{symbol}-1m.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                if header:
                    writer.writerow(HEADER)
                for clock in clocks[symbol]:
                    writer.writerow(row(clock, 100.0 + offset))
            result[symbol] = path
        return result

    def complete_clocks(self, count: int = 2) -> dict[str, list[int]]:
        start = 1_704_067_200_000
        clocks = [start + index * 60_000 for index in range(count)]
        return {symbol: clocks.copy() for symbol in SYMBOLS}

    def test_builds_four_crypto_perpetuals(self) -> None:
        instruments = make_binance_usdm_instruments()
        self.assertEqual(tuple(instruments), SYMBOLS)
        self.assertTrue(all(isinstance(item, CryptoPerpetual) for item in instruments.values()))
        self.assertEqual(str(instruments["BTCUSDT"].id), "BTCUSDT-PERP.BINANCE")

    def test_streams_native_bars_and_preserves_policy_flow(self) -> None:
        loader = BinanceKline1mLoader(self.sources(self.complete_clocks()))
        minutes = list(loader)
        self.assertEqual(len(minutes), 2)
        first = minutes[0]
        expected_close_ns = (1_704_067_200_000 + 60_000) * 1_000_000
        self.assertEqual(first.ts_event, expected_close_ns)
        self.assertEqual(first.bars["BTCUSDT"].ts_event, expected_close_ns)
        self.assertEqual(str(first.bars["BTCUSDT"].bar_type), "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        self.assertEqual(float(first.bars["BTCUSDT"].volume), 10.0)
        flow = first.flows["BTCUSDT"]
        self.assertEqual(flow.source_close_time_ns, (1_704_067_200_000 + 59_999) * 1_000_000)
        self.assertEqual(flow.quote_volume, 1005.0)
        self.assertEqual(flow.taker_buy_volume, 6.0)
        self.assertEqual(flow.taker_buy_quote_volume, 603.0)
        self.assertEqual(flow.trade_count, 20)
        self.assertEqual(flow.signed_quote_flow, 201.0)

    def test_accepts_raw_vision_rows_without_header(self) -> None:
        loader = BinanceKline1mLoader(self.sources(self.complete_clocks(1), header=False))
        minute = next(iter(loader))
        self.assertEqual(set(minute.bars), set(SYMBOLS))

    def test_streams_official_monthly_zip_members_directly(self) -> None:
        csv_sources = self.sources(self.complete_clocks(1), header=False)
        zip_sources: dict[str, Path] = {}
        for symbol, csv_path in csv_sources.items():
            zip_path = self.root / f"{symbol}-1m-2024-01.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(csv_path, arcname=f"{symbol}-1m-2024-01.csv")
            csv_path.unlink()
            zip_sources[symbol] = zip_path
        minute = next(iter(BinanceKline1mLoader(zip_sources)))
        self.assertEqual(tuple(minute.bars), SYMBOLS)

    def test_rejects_duplicate_and_gap(self) -> None:
        duplicate = self.complete_clocks()
        duplicate["BTCUSDT"].insert(1, duplicate["BTCUSDT"][0])
        with self.assertRaisesRegex(BinanceKlineDataError, "duplicate"):
            list(BinanceKline1mLoader(self.sources(duplicate)))

        gap = self.complete_clocks(3)
        for symbol in SYMBOLS:
            gap[symbol].pop(1)
        with self.assertRaisesRegex(BinanceKlineDataError, "gap"):
            list(BinanceKline1mLoader(self.sources(gap)))

    def test_rejects_partial_synchronized_clock(self) -> None:
        clocks = self.complete_clocks()
        clocks["XRPUSDT"].pop()
        with self.assertRaisesRegex(BinanceKlineDataError, "partial synchronized clock"):
            list(BinanceKline1mLoader(self.sources(clocks)))

        clocks = self.complete_clocks()
        clocks["XRPUSDT"] = [value + 60_000 for value in clocks["XRPUSDT"]]
        with self.assertRaisesRegex(BinanceKlineDataError, "partial synchronized clock"):
            list(BinanceKline1mLoader(self.sources(clocks)))

    def test_catalog_and_flow_sidecar_round_trip_in_bounded_chunks(self) -> None:
        loader = BinanceKline1mLoader(self.sources(self.complete_clocks(3)))
        catalog_path = self.root / "catalog"
        result = write_parquet_catalog(loader, catalog_path, chunk_size=4)
        self.assertEqual(result.synchronized_minutes, 3)
        self.assertEqual(result.bars_written, 12)
        queried = query_catalog_bars(catalog_path)
        self.assertEqual({symbol: len(bars) for symbol, bars in queried.items()}, {symbol: 3 for symbol in SYMBOLS})
        flows = list(iter_policy_flow_sidecar(result.flow_sidecar))
        self.assertEqual(len(flows), 12)
        self.assertEqual(flows[0].ts_event, queried["BTCUSDT"][0].ts_event)


if __name__ == "__main__":
    unittest.main()
