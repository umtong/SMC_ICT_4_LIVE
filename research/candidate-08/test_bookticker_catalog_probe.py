"""Contracts for the official Binance Vision bookTicker catalog probe."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlparse

from bookticker_catalog_probe import (
    CATALOG_REVISION,
    CatalogObject,
    _contiguous_week_starts,
    build_catalog_evidence,
    list_catalog_objects,
)
from data import BinanceDataError


S3_XML_TEMPLATE = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\">
  <Name>data.binance.vision</Name>
  <Prefix>{prefix}</Prefix>
  <KeyCount>{key_count}</KeyCount>
  <MaxKeys>1000</MaxKeys>
  <IsTruncated>{is_truncated}</IsTruncated>
  {contents}
  {next_token}
</ListBucketResult>
"""


def _content(key: str, *, size: int = 1000, modified: str = "2025-01-01T00:00:00.000Z") -> str:
    return (
        "<Contents>"
        f"<Key>{key}</Key>"
        f"<LastModified>{modified}</LastModified>"
        '<ETag>\"0123456789abcdef0123456789abcdef\"</ETag>'
        f"<Size>{size}</Size>"
        "<StorageClass>STANDARD</StorageClass>"
        "</Contents>"
    )


def _catalog_pair(symbol: str, day: date, *, size: int = 1000) -> list[CatalogObject]:
    prefix = f"data/futures/um/daily/bookTicker/{symbol}/"
    filename = f"{symbol}-bookTicker-{day.isoformat()}.zip"
    archive = CatalogObject(
        key=f"{prefix}{filename}",
        size=size,
        last_modified="2025-01-01T00:00:00.000Z",
        etag="archive-etag",
    )
    checksum = CatalogObject(
        key=f"{archive.key}.CHECKSUM",
        size=96,
        last_modified="2025-01-01T00:00:01.000Z",
        etag="checksum-etag",
    )
    return [archive, checksum]


class BookTickerCatalogContracts(unittest.TestCase):
    def test_paginated_s3_listing_uses_exact_prefix_and_continuation_token(self) -> None:
        prefix = "data/futures/um/daily/bookTicker/BTCUSDT/"
        first_key = f"{prefix}BTCUSDT-bookTicker-2025-01-01.zip"
        second_key = f"{first_key}.CHECKSUM"
        requested_urls: list[str] = []

        def downloader(url: str, *, timeout: int = 180) -> bytes:
            del timeout
            requested_urls.append(url)
            query = parse_qs(urlparse(url).query)
            self.assertEqual(query["list-type"], ["2"])
            self.assertEqual(query["prefix"], [prefix])
            self.assertEqual(query["max-keys"], ["1000"])
            token = query.get("continuation-token", [None])[0]
            if token is None:
                xml = S3_XML_TEMPLATE.format(
                    prefix=prefix,
                    key_count=1,
                    is_truncated="true",
                    contents=_content(first_key),
                    next_token="<NextContinuationToken>page-2-token</NextContinuationToken>",
                )
            else:
                self.assertEqual(token, "page-2-token")
                xml = S3_XML_TEMPLATE.format(
                    prefix=prefix,
                    key_count=1,
                    is_truncated="false",
                    contents=_content(second_key, size=96),
                    next_token="",
                )
            return xml.encode("utf-8")

        objects = list_catalog_objects(symbol="BTCUSDT", downloader=downloader)

        self.assertEqual([item.key for item in objects], [first_key, second_key])
        self.assertEqual(len(requested_urls), 2)
        self.assertNotIn("continuation-token", parse_qs(urlparse(requested_urls[0]).query))
        self.assertEqual(
            parse_qs(urlparse(requested_urls[1]).query)["continuation-token"],
            ["page-2-token"],
        )

    def test_contiguous_week_starts_require_every_calendar_day(self) -> None:
        start = date(2025, 1, 1)
        days = {start + timedelta(days=offset) for offset in range(10)}
        self.assertEqual(
            _contiguous_week_starts(days),
            [start + timedelta(days=offset) for offset in range(4)],
        )
        days.remove(start + timedelta(days=4))
        self.assertEqual(
            _contiguous_week_starts(days),
            [start + timedelta(days=5), start + timedelta(days=6), start + timedelta(days=7), start + timedelta(days=8), start + timedelta(days=9)] if False else [],
        )

    def test_seeded_selection_is_reproducible_and_outcome_blind(self) -> None:
        symbol = "BTCUSDT"
        start = date(2025, 1, 1)
        objects: list[CatalogObject] = []
        for offset in range(20):
            objects.extend(_catalog_pair(symbol, start + timedelta(days=offset), size=1000 + offset))

        first = build_catalog_evidence(symbol=symbol, seed=8811, objects=objects)
        second = build_catalog_evidence(symbol=symbol, seed=8811, objects=list(reversed(objects)))
        third = build_catalog_evidence(symbol=symbol, seed=8812, objects=objects)

        self.assertEqual(first, second)
        self.assertNotEqual(
            first["selection"]["selected_windows"],
            third["selection"]["selected_windows"],
        )
        self.assertEqual(first["catalog_revision"], CATALOG_REVISION)
        self.assertTrue(first["selection"]["outcome_blind"])
        self.assertEqual(first["selection"]["seed"], 8811)
        self.assertEqual(first["availability"]["daily_archives"], 20)
        self.assertEqual(first["availability"]["contiguous_week_starts"], 14)
        self.assertEqual(len(first["selection"]["selected_windows"]), 3)
        for window in first["selection"]["selected_windows"]:
            self.assertEqual(len(window["days"]), 7)
            self.assertEqual(
                date.fromisoformat(window["end_exclusive"])
                - date.fromisoformat(window["start"]),
                timedelta(days=7),
            )

    def test_missing_checksum_is_rejected_before_week_selection(self) -> None:
        symbol = "BTCUSDT"
        start = date(2025, 1, 1)
        objects: list[CatalogObject] = []
        for offset in range(7):
            objects.extend(_catalog_pair(symbol, start + timedelta(days=offset)))
        objects = [item for item in objects if not item.key.endswith("2025-01-04.zip.CHECKSUM")]
        with self.assertRaisesRegex(BinanceDataError, "without CHECKSUM"):
            build_catalog_evidence(symbol=symbol, seed=8811, objects=objects)

    def test_no_contiguous_week_is_rejected(self) -> None:
        symbol = "BTCUSDT"
        start = date(2025, 1, 1)
        objects: list[CatalogObject] = []
        for offset in (0, 1, 2, 4, 5, 6, 8, 9):
            objects.extend(_catalog_pair(symbol, start + timedelta(days=offset)))
        with self.assertRaisesRegex(BinanceDataError, "no contiguous seven-day"):
            build_catalog_evidence(symbol=symbol, seed=8811, objects=objects)

    def test_catalog_source_contains_no_strategy_or_backtest(self) -> None:
        source = Path(__file__).with_name("bookticker_catalog_probe.py").read_text(
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
            "profit_factor",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
